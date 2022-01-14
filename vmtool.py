#!/usr/bin/env python
# -*- conding: utf-8 -*-

import os
import argparse
import xml.etree.ElementTree as ET
import sys
import string
import tempfile
import datetime
import copy


from subprocess import (Popen, PIPE)
try:
    from urllib.request import urlopen
except ImportError:
    from urllib2 import urlopen

xml_template_url = os.environ.get('KVM_XML_TEMPLATE') or 'http://192.168.2.56/cobbler/ks_mirror/bash/template.xml'
xml_template_local_path = os.environ.get('KVM_XML_PATH') or '/etc/libvirt/qemu/'


def get_xml_file(xml_filename):
    full_path = os.path.join(xml_template_local_path, xml_filename)
    req = urlopen(xml_template_url)
    data = req.read()
    with open(full_path, 'wb') as xml_file:
        xml_file.write(data)
    return full_path


def modify_xml_file(filename, vmname, imgname, cdrom=None):
    xmltree = ET.parse(filename)
    elementroot = xmltree.getroot()
    domain_name = elementroot.find('name')
    disk_node = elementroot.find('devices')
    domain_name.text = vmname
    disk_node[1][2].set('name', imgname)
    if cdrom:
        os_element = elementroot.find('os')
        os_element[2].set('dev', 'cdrom')
        cd_element = ET.Element('disk', attrib={'type': 'file', 'device': 'cdrom'})
        cd_subelement_driver = ET.Element('driver', attrib={'name': 'qemu', 'type': 'raw'})
        cd_subelement_target = ET.Element('target', attrib={'dev': 'hdb', 'bus': 'ide'})
        cd_subelement_readonly = ET.Element('readonly')
        cd_subelement_source = ET.Element('source', attrib={'file': cdrom})
        cd_subelement_address = ET.Element('address',
                                           attrib={'type': 'drive', 'controller': '0', 'bus': '0', 'target': '0',
                                                   'unit': '1'})
        cd_element.append(cd_subelement_driver)
        cd_element.append(cd_subelement_source)
        cd_element.append(cd_subelement_target)
        cd_element.append(cd_subelement_readonly)
        cd_element.append(cd_subelement_address)
        disk_node.append(cd_element)
    xmltree.write(filename)


def has_exist_img(img, _type):
    if _type == 'network':
        command = 'rbd -p kvm list |grep %s' % img.split('/')[1]
        result = Popen(command, stdout=PIPE, stderr=PIPE, shell=True)

        return True if result.stdout.read().strip().decode() else False
    else:
        return os.path.exists(img)


def has_exist_vm(vmname):
    command = 'virsh list --all |grep %s' % vmname
    result = Popen(command, stdout=PIPE, stderr=PIPE, shell=True)
    return True if result.stdout.read().strip().decode() else False


def create_img(img, size):
    command = 'qemu-img create -f rbd rbd:{0} {1}'.format(img, size)
    os.system(command)


def create_local_img(path, size):
    command = 'qemu-img create -f qcow2 -o size=%s %s' % (size, path)
    os.system(command)


def create_vm(args):
    if not has_exist_img(args.disk, 'network') and not has_exist_vm(args.name):
        xml_file = get_xml_file(args.name + '.xml')
        modify_xml_file(xml_file, args.name, args.disk, cdrom=args.cdrom)
        create_img(args.disk, args.size)
        command = 'virsh define {0}'.format(xml_file)
        os.system(command)
        _start_vm(args.name)
        port = get_vnc_port(args.name)
        if port:
            print('The VM vnc port is: %s' % port)
        else:
            print('The new create vm %s not started!' % args.name)
    else:
        print('Could not create VM, the vm name or image name is already existed!')


def _start_vm(vmname):
    command = 'virsh start %s' % vmname
    os.system(command)


def start_vm(args):
    arg = []
    if args.console:
        arg.append('--console ')
    if args.force_boot:
        arg.append('--force-boot')
    for vm in args.name:
        arg.append(vm)
        _start_vm(' '.join(arg))


def _stop_vm(vmname):
    command = 'virsh destroy %s' % vmname
    os.system(command)


def stop_vm(args):
    arg = []
    if args.graceful:
        arg.append('--graceful')
    for vm in args.name:
        arg.append(vm)
        _stop_vm(' '.join(arg))


def _shutdown_vm(vmname):
    command = 'virsh shutdown %s' % vmname
    os.system(command)


def shutdown_vm(args):
    arg = []
    if args.mode:
        arg += ['--mode', args.mode]
    for vm in args.name:
        arg.append(vm)
        _shutdown_vm(' '.join(arg))


def list_vm(args):
    command = ['virsh', 'list']

    if args.all:
        command.append('--all')
    if args.inactive:
        command.append('--inactive')
    if args.autostart:
        command.append('--autostart')
    if args.state_shutoff:
        command.append('--state-shutoff')
    if args.with_snapshot:
        command.append('--with-snapshot')
    if args.without_snapshot:
        command.append('--without-snapshot')
    os.system(' '.join(command))


def get_vnc_port(vmname):
    command = "netstat -tnlp | grep `ps -fu qemu |grep %s |grep -v 'grep' | awk '{print $2}'` | awk '{print $4}' | awk -F: '{print $2}'" % vmname

    result = Popen(command, stdout=PIPE, stderr=PIPE, shell=True)
    return result.stdout.read().strip().decode()


def check_vm_state(vm):
    command = 'virsh domstate %s' % vm
    state = Popen(command, stdout=PIPE, stderr=PIPE, shell=True)
    return state.stdout.read().strip().decode()


def find_disk(xml_file):
    xmltree = ET.parse(xml_file)
    elementroot = xmltree.getroot()
    local_disk = []
    network_disk = []
    for e in elementroot.find('devices'):
        if e.tag == 'disk':
            for s in e:
                if s.tag == 'source':
                    if e.get('type') == 'file':
                        local_disk.append(s.get('file'))
                    elif e.get('type') == 'block':
                        local_disk.append(s.get('dev'))
                    elif e.get('type') == 'network':
                        network_disk.append(s.get('name'))

    return {'network': network_disk, 'local': local_disk}


def remove_network_disk(img):
    command = 'rbd remove --pool {0[0]} --image {0[1]} --no-progress'.format(img.split('/'))
    os.system(command)


def remove_local_disk(img):
    os.remove(img)


def remove_vm(args):
    vm_state = check_vm_state(args.name)
    if vm_state == 'shut off':
        xml_filename = os.path.join(xml_template_local_path, args.name + '.xml')
        img_disk = find_disk(xml_filename)
        if img_disk['local']:
            for d in img_disk['local']:
                remove_local_disk(d)

        if img_disk['network']:
            for d in img_disk['network']:
                remove_network_disk(d)

        command = 'virsh undefine {0}'.format(args.name)
        os.system(command)
    else:
        print('This vm is %s,please turn off the vm first!' % vm_state)


def generate_tag(xml):
    tree = ET.parse(xml)
    root = tree.getroot()
    alphabet = {'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z'}
    tag = set()
    for d in root.iter('disk'):
        tag.add(d.find('target').get('dev')[-1])
    return 'vd' + list(alphabet.difference(tag))[0]


def generate_disk_xml(path, img, tag):
    template = '''
<disk type='network' device='disk'>
  <driver name='qemu' type='raw' cache='none'/>
  <auth username='kvm'>
    <secret type='ceph' uuid='7dbfa60d-5418-40b1-a330-058f0be1300b'/>
  </auth>
  <source protocol='rbd' name='kvm/dev-test.img'>
     <host name='10.11.11.2' port='6789'/>
     <host name='10.11.11.3' port='6789'/>
     <host name='10.11.11.4' port='6789'/>
  </source>
  <target dev='vdb' bus='virtio'/>
</disk>
'''
    root = ET.fromstring(template)
    root.find('source').set('name', img)
    root.find('target').set('dev', tag)
    with open(path, 'w') as xml:
        xml.write(ET.tostring(root, encoding='unicode'))


def add_disk(args):
    xml_file = os.path.join(xml_template_local_path, args.name + '.xml')
    new_tag = generate_tag(xml_file)
    if args.type == 'network':
        if not has_exist_img(args.disk, args.type):
            create_img(args.disk, args.size)

        with tempfile.NamedTemporaryFile() as tf:
            generate_disk_xml(tf.name, args.disk, new_tag)
            os.system('virsh attach-device %s %s --persistent' % (args.name, tf.name))
    else:
        if not has_exist_img(args.disk, args.type):
            create_local_img(args.disk, args.size)

        command = 'virsh attach-disk %s %s %s --targetbus virtio --driver qemu --subdriver qcow2 --sourcetype file --cache none --persistent' % (args.name, args.disk, new_tag)
        os.system(command)


def del_disk(args):
    xml_file = os.path.join(xml_template_local_path, args.name + '.xml')
    tree = ET.parse(xml_file)
    root = tree.getroot()
    img = None
    d_type = None
    for d in root.iter('disk'):
        if d.find('target').get('dev') == args.tag:
            if d.get('type') == 'network':
                img = d.find('source').get('name')
                d_type = 'network'
            elif d.get('type') == 'file':
                img = d.find('source').get('file')
                d_type = 'file'
            elif d.get('type') == 'block':
                img = d.find('source').get('dev')
                d_type = 'block'
            break

    if d_type == 'network':
        with tempfile.NamedTemporaryFile() as tf:
            generate_disk_xml(tf.name, img, args.tag)
            os.system('virsh detach-device %s %s --persistent' % (args.name, tf.name))
    else:
        os.system('virsh detach-disk %s %s --persistent' % (args.name, args.tag))

    if args.thorough:
        if d_type == 'network':
            remove_network_disk(img)
        else:
            remove_local_disk(img)
        print('Disk %s has been deleted from %s,this operation is not recoverable!' % (img, args.name))
    else:
        print('Disk %s has been detached from %s, this operation can recoverable!' % (img, args.name))


def set_vcpus(args):
    vm_state = check_vm_state(args.name)
    if vm_state == 'shut off':
        command1 = 'virsh setvcpus %s %s --maximum --config' % (args.name, args.count)
        command2 = 'virsh setvcpus %s %s --current' % (args.name, args.count)
        command3 = 'virsh start %s' % args.name
        os.system(command1)
        os.system(command2)
        os.system(command3)
    else:
        print('Operation not supported: please turn off the vm first')


def set_memory(args):
    vm_state = check_vm_state(args.name)
    last_str = args.size[-1]
    unit = last_str if last_str in 'GMKT' else 'K'
    convert = {'G': 1024 * 1024, 'M': 1024, 'K': 1, 'T': 1024 * 1024 * 1024}
    old_size = int(args.size[:-1]) if last_str not in string.digits else int(args.size)
    size = old_size * convert[unit]
    if vm_state == 'shut off':
        command1 = 'virsh setmaxmem %s %s --config' % (args.name, size)
        command2 = 'virsh setmem %s %s --current' % (args.name, size)
        command3 = 'virsh start %s' % args.name
        os.system(command1)
        os.system(command2)
        os.system(command3)
    else:
        print('Operation not supported: please turn off the vm first')


def _create_snap(image_spec):
    tag = datetime.date.strftime(datetime.date.today(), '%Y%m%d')
    snapshot_name = '{0}@{1}'.format(image_spec, tag)
    command = 'rbd snap create {}'.format(snapshot_name)
    os.system(command=command)

    # return <string>  eg: kvm/test.img@111
    return str(snapshot_name)


def snap_create(args):
    pool = args.pool
    image_spec = args.snap_spec('/')
    if not pool:
        pool = image_spec[0]
    _create_snap(pool + '/' + image_spec[-1])


def _del_snap(snapshot_name):
    command = 'rbd snap remove {}'.format(snapshot_name)
    os.system(command)


def snap_remove(args):
    pool = args.pool
    snap_spec = args.snap_spec
    if pool:
        snap_spec = pool + '/' + snap_spec[-1]
    _del_snap(snap_spec)


def list_snap(args):
    command = ['rbd', 'snap', 'list']
    image_spec = args.image_spec
    if args.pool:
        image_spec = args.pool + '/' + image_spec.split('/')[-1]
    command.append(image_spec)
    os.system(' '.join(command))


def _protect_snap(snap):
    command = 'rbd snap protect {}'.format(snap)
    os.system(command)


def _unprotect_snap(snap):
    command = 'rbd snap unprotect {}'.format(snap)
    os.system(command)


def _flatten_image(image_spec):
    command = 'rbd flatten {}'.format(image_spec)
    os.system(command)


def clone_snap_to_img(snapshot_name, dest_pool, new_image):
    command = 'rbd clone {0} --dest-pool {1} --dest {2}'.format(snapshot_name, dest_pool, new_image)
    os.system(command)
    return str(dest_pool + '/' + new_image)


def _clone_from_vm_name(src_name, new_name, dest_pool=None):
    vm_xml = os.path.join(xml_template_local_path, src_name + '.xml')
    xmltree = ET.parse(vm_xml)
    elementroot = xmltree.getroot()
    dest_pool = dest_pool
    devices_ele = elementroot.find('devices')
    i = 0
    for element in devices_ele.findall('disk'):
        src_image_spec = element.find('source').get('name')
        src_image_snap = _create_snap(src_image_spec)
        _protect_snap(src_image_snap)
        if not dest_pool:
            dest_pool = src_image_spec.split('/')[0]
        dest_img = clone_snap_to_img(src_image_snap, dest_pool, new_name + '-' + str(i) + '.img')
        _flatten_image(dest_img)
        _unprotect_snap(src_image_snap)
        _del_snap(src_image_snap)
        element.find('source').set('name', dest_img)

    for interface_ele in devices_ele.findall('interface'):
        interface_ele.remove(interface_ele.find('mac'))
    dest_vm_xml = os.path.join(xml_template_local_path, new_name + '.xml')
    xmltree.write(dest_vm_xml)

    return dest_vm_xml


def _clone_from_image(src_pool, src_image, new_name, dest_pool):
    new_vm_xml = get_xml_file(new_name + '.xml')
    src_image_snap = _create_snap(src_pool + '/' + src_image)
    _protect_snap(src_image_snap)
    dest_img = clone_snap_to_img(src_image_snap, dest_pool, new_name + '.img')
    _flatten_image(dest_img)
    _unprotect_snap(src_image_snap)
    _del_snap(src_image_snap)
    modify_xml_file(new_vm_xml, new_name, dest_img)

    return new_vm_xml


def _clone_from_snapshot(src_pool, src_snapshot, new_name, dest_pool):
    new_vm_xml = get_xml_file(new_name + '.xml')
    src_image_snap = src_pool + '/' + src_snapshot
    _protect_snap(src_image_snap)
    dest_img = clone_snap_to_img(src_image_snap, dest_pool, new_name + '.img')
    _flatten_image(dest_img)
    _unprotect_snap(src_image_snap)
    modify_xml_file(new_vm_xml, new_name, dest_img)

    return new_vm_xml


def clone_vm(args):
    src_spec = args.src_spec
    src_pool = args.pool
    dest_pool = args.dest_pool
    src_img_spec = src_spec.split('/')

    if not src_pool:
        src_pool = src_img_spec[0]

    if not dest_pool:
        dest_pool = src_pool

    if src_spec.endswith('.img'):
        new_vm_xml_file = _clone_from_image(src_pool, src_img_spec[-1], args.new_name, dest_pool)
    elif not src_spec.endswith('.img') and '@' in src_spec:
        new_vm_xml_file = _clone_from_snapshot(src_pool, src_img_spec[-1], args.new_name, dest_pool)
    else:
        new_vm_xml_file = _clone_from_vm_name(src_img_spec, args.new_name, dest_pool=dest_pool)

    command = 'virsh define {0}'.format(new_vm_xml_file)

    os.system(command)
    _start_vm(args.new_name)
    port = get_vnc_port(args.new_name)

    if port:
        print('The VM vnc port is: %s' % port)
    else:
        print('The new vm %s is not started!' % args.new_name)


def main():
    parser = argparse.ArgumentParser(prog='vmtool', description='KVM oprations command-line tool',
                                     epilog='Run %(prog)s <command> -h/--help for help on a specific command.')
    subparser = parser.add_subparsers(title='subcommands', description='valid subcommands', metavar='<command>')

    parser_create = subparser.add_parser('create', help='Create a new KVM guest instance from the specified XML file.')
    parser_create.add_argument('-n', '--name', help='Name of the guest instance. Ex: -n/--name test01',
                               required=True, metavar='<name>')
    parser_create.add_argument('-d', '--disk', help='Specify image on Ceph Cluster. Ex: -d/--disk kvm/test01.img',
                               required=True, metavar='<disk>')
    parser_create.add_argument('-s', '--size', help='Specify the storage size. Ex: -s/--size 10G,(default: 10G)',
                               default='10G', metavar='<size>')
    parser_create.add_argument('-c', '--cdrom', metavar='<cdrom>',
                               help='CD-ROM installation media. Ex: -c/--cdrom /kvm/CentOS-7.2-x86_64-DVD-1511.iso')
    parser_create.set_defaults(func=create_vm)

    parser_remove = subparser.add_parser('remove', help='Delete a KVM guest instance.')
    parser_remove.add_argument('name', help='Name of the guest instance. Ex: test01', metavar='<name>')
    parser_remove.set_defaults(func=remove_vm)

    parser_clone = subparser.add_parser('clone', help='Clone a new KVM guest instance form the specified image or image snapshot or instance name.')
    parser_clone.add_argument('-p', '--pool', metavar='<src-pool>', help='source pool name.')
    parser_clone.add_argument('--dest-pool', metavar='<dest-pool>', help='destination pool name. The default value is the same as the source pool')
    parser_clone.add_argument('src_spec', metavar='<src-image-or-snapshot-or-VMName>',
                              help='source image or snapshot or vm name specification(example: [<pool-name>/]<image-name> or '
                              '[<pool-name>/]<image-name>@<snapshot-name> or <VMName>)')
    parser_clone.add_argument('new_name', metavar='<new-name>', help='New KVM guest instance name.')
    parser_clone.set_defaults(func=clone_vm)

    parser_list = subparser.add_parser('list', help='list KVM guest instance.')
    parser_list.add_argument('--all', help='list inactive & active instance.', action='store_true')
    parser_list.add_argument('--inactive', help='list inactive instance.', action='store_true')
    parser_list.add_argument('--with-snapshot', help='list instance with existing snapshot.',
                             action='store_true', dest='with_snapshot')
    parser_list.add_argument('--without-snapshot', help='list instance without a snapshot.',
                             action='store_true', dest='without_snapshot')
    parser_list.add_argument('--state-shutoff', help='list instance in shutoff state.',
                             action='store_true', dest='state_shutoff')
    parser_list.add_argument('--autostart', help='list instance with autostart enabled.', action='store_true')
    parser_list.set_defaults(func=list_vm)

    parser_start = subparser.add_parser('start', help='start (previously defined)given inactive instance.')
    parser_start.add_argument('name', help='some instance name.(example: test1 test2 test3)', metavar='<name>', nargs=argparse.REMAINDER)
    parser_start.add_argument('--console', help='attach to console after creation.', action='store_true')
    parser_start.add_argument('--force-boot', help='force fresh boot by discarding any managed save.',
                              dest='force_boot', action='store_true')
    parser_start.set_defaults(func=start_vm)

    parser_stop = subparser.add_parser('stop', help='Forcefully stop given domain, but leave its resources intact.')
    parser_stop.add_argument('--graceful', help='terminate gracefully', action='store_true')
    parser_stop.add_argument('name', help='some instance name.(example: test1 test2 test3)', metavar='<name>', nargs=argparse.REMAINDER)
    parser_stop.set_defaults(func=stop_vm)

    parser_shutdown = subparser.add_parser('shutdown', help='Gracefully shutdown the given instance.')
    parser_shutdown.add_argument('--mode', metavar='<mode string>', help='shutdown mode: acpi|agent|initctl|signal|paravirt')
    parser_shutdown.add_argument('name', help='some instance name.(example: test1 test2 test3)', metavar='<name>', nargs=argparse.REMAINDER)
    parser_shutdown.set_defaults(func=shutdown_vm)

    parser_add_disk = subparser.add_parser('add-disk', help='Domain add a new disk.')
    parser_add_disk.add_argument('name', help='Name of the guest instance. Ex: test01', metavar='<name>')
    parser_add_disk.add_argument('-d', '--disk', help='Specify image on Ceph Cluster. Ex: -d/--disk kvm/test01.img',
                                 required=True, metavar='<disk>')
    parser_add_disk.add_argument('-s', '--size', help='Specify the storage size. Ex: -s/--size 10G,(default: 10G)',
                                 default='10G', metavar='<size>')
    parser_add_disk.add_argument('-t', '--type', choices=['network', 'local'], required=True, metavar='<type>',
                                 help='Specify the storage type(network|local).')
    parser_add_disk.set_defaults(func=add_disk)

    parser_del_disk = subparser.add_parser('del-disk', help="Delete specified domain's a disk.")
    parser_del_disk.add_argument('name', help='Name of the guest instance. Ex: test01', metavar='<name>')
    parser_del_disk.add_argument('-t', '--tag', help='target of disk device on os. Ex: vdb', required=True, metavar='<tag>')
    parser_del_disk.add_argument('-T', '--thorough',
                                 help='disk will be thorough delete, if value is True.(default: False)', default=False,
                                 metavar='<thorough>')
    parser_del_disk.set_defaults(func=del_disk)

    parser_set_vcpu = subparser.add_parser('setcpu', help='Change the number of virtual CPUs in the guest domain.')
    parser_set_vcpu.add_argument('name', help='Name of the guest instance. Ex: test01', metavar='<name>')
    parser_set_vcpu.add_argument('count', help='number of virtual CPUs', metavar='<count>')
    parser_set_vcpu.set_defaults(func=set_vcpus)

    parser_set_memory = subparser.add_parser('setmem', help='Change the current memory allocation in the guest domain.')
    parser_set_memory.add_argument('name', help='Name of the guest instance. Ex: test01', metavar='<name>')
    parser_set_memory.add_argument('size', help='new memory size, Ex: 2G or 1024M (default unit K)', metavar='<size>')
    parser_set_memory.set_defaults(func=set_memory)

    parser_snap_create = subparser.add_parser('create-snap', help='Create a image snapshot.')
    parser_snap_create.add_argument('-p', '--pool', help='pool name.', metavar='<pool_name>')
    parser_snap_create.add_argument('snap_spec', metavar='<snap-spec>',
                                    help='snapshot specification.(example: [<pool-name>/]<image-name>@<snapshot-name>)')
    parser_snap_create.set_defaults(func=snap_create)

    parser_snap_remove = subparser.add_parser('remove-snap', help='Delete a image snapshot.')
    parser_snap_remove.add_argument('-p', '--pool', help='pool name.', metavar='<pool_name>')
    parser_snap_remove.add_argument('snap_spec',  metavar='<snap-spec>',
                                    help='snapshot specification.(example: [<pool-name>/]<image-name>@<snapshot-name>)')
    parser_snap_remove.set_defaults(func=snap_remove)

    parser_snap_list = subparser.add_parser('list-snap', help='Dump list of image snapshots.')
    parser_snap_list.add_argument('-p', '--pool', help='pool name.', metavar='<pool_name>')
    parser_snap_list.add_argument('image_spec', metavar='<image-spec>',
                                  help='image specification(example: [<pool-name>/]<image-name>)')
    parser_snap_list.set_defaults(func=list_snap)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    # filename = '/root/template.xml'
    # filename = get_xml_file('template.xml')
    # cdrom = '/kvm/CentOS-7.2-x86_64-DVD-1511.iso'
    # modify_xml_file(filename, 'test01', 'kvm/test02', cdrom=cdrom)
    main()
