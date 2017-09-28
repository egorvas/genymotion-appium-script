# -*- coding: utf-8 -*-

import boto3
import docker
import argparse
from threading import Thread
import time

POOLING_TIMEOUT = 30
EC2 = 'ec2'
START_PORT = 6000
DOCKER_IMAGE_NAME = 'egorvas/appium-docker-android'




def stop(name, postfix, region, aws_secret, aws_key):
    resource = boto3.resource(EC2, region_name=region,
                              aws_access_key_id=aws_secret, aws_secret_access_key=aws_key)
    client = boto3.client(EC2, region_name=region,
                          aws_access_key_id=aws_secret, aws_secret_access_key=aws_key)
    instances = resource.instances.filter(Filters=[{'Name': 'tag:Name', 'Values': [name + postfix]}])
    if sum(1 for _ in instances.all()) > 0:
        client.stop_instances(InstanceIds=[instance.id for instance in instances])
        client.create_tags(Resources=[instance.id for instance in instances],
                           Tags=[{'Key': 'Name', 'Value': name}])
    delete_containers(name, postfix)


def create(name, postfix, instance_type, ami, number_of_instances, region, key_name, security_group, subnet_id,
           aws_secret, aws_key, selenium_host, selenium_port, version, volume_path, network):
    resource = boto3.resource(EC2, region_name=region, aws_access_key_id=aws_secret,
                              aws_secret_access_key=aws_key)
    client = boto3.client(EC2, region_name=region,
                          aws_access_key_id=aws_secret, aws_secret_access_key=aws_key)
    ready_instances = get_ready_instances(resource, name)
    number_of_ready_instances = sum(1 for _ in ready_instances.all())

    if number_of_ready_instances >= number_of_instances:
        start_instances(client, name+postfix, ready_instances, number_of_instances)
    else:
        number_of_stopping_instances = get_number_of_stopping_instances(resource, name)
        if number_of_stopping_instances >= (number_of_instances - number_of_ready_instances):
            wait_for_stopping_instances(resource, name, number_of_stopping_instances)
            start_instances(client, name + postfix, get_ready_instances(resource, name),
                            number_of_instances-number_of_ready_instances)
        else:
            if number_of_stopping_instances > 0:
                wait_for_stopping_instances(resource, name, number_of_stopping_instances)
                start_instances(client, name + postfix, get_ready_instances(resource, name),
                                number_of_stopping_instances)
            create_instances(resource, ami, key_name,
                             number_of_instances-(number_of_ready_instances+number_of_stopping_instances),
                             instance_type,security_group, subnet_id, name+postfix)
        if number_of_ready_instances > 0:
            start_instances(client, name + postfix, ready_instances, number_of_ready_instances)

    ips_of_instances = get_ips_of_instances(resource, name+postfix)
    docker_client = docker.from_env()


    for index, ip in enumerate(ips_of_instances):
        Thread(target=run_container, args=(name, postfix, ip,selenium_host, selenium_port, version, volume_path,
                                           docker_client,
                                           get_last_available_port(docker_client)+index, network)).start()



def wait_for_stopping_instances(resource, name, number_of_stopping_instances):
   while number_of_stopping_instances > 0:
       time.sleep(POOLING_TIMEOUT)
       number_of_stopping_instances = get_number_of_stopping_instances(resource, name)


def get_ready_instances(resource, name):
    return resource.instances.filter(Filters=[{'Name': 'tag:Name', 'Values': [name]},
                                                   {'Name': 'instance-state-name', 'Values': ['stopped']}])


def get_number_of_stopping_instances(resource, name):
    stopping_instances = resource.instances.filter(Filters=[{'Name': 'tag:Name', 'Values': [name]},
                                                   {'Name': 'instance-state-name', 'Values': ['stopping']}])
    return sum(1 for _ in stopping_instances.all())


def start_instances(client, title, ready_instances, number):
    ids = [instance.id for instance in ready_instances][:number]
    client.create_tags(Resources=ids,Tags=[{'Key': 'Name', 'Value': title}])
    client.start_instances(InstanceIds=ids)
    return ids


def create_instances(resource, ami, key_name, number, instance_type, security_group, subnet_id, title):
    new_instances = resource.create_instances(ImageId=ami, MinCount=1, KeyName=key_name,
                                              MaxCount=number,
                                              InstanceType=instance_type, SecurityGroupIds=[security_group],
                                              SubnetId=subnet_id,
                                              TagSpecifications=[{'ResourceType': 'instance',
                                                                  'Tags': [{'Key': 'Name',
                                                                            'Value': title}]}])
    return new_instances


def get_last_available_port(docker_client):
    containers = docker_client.containers.list(all=True, filters={"ancestor":DOCKER_IMAGE_NAME})
    ports = []
    for container in containers:
        ports.append(int(container.name.split('_')[-1]))
    start_port = START_PORT
    if len(ports)>0:
        start_port = sorted(ports)[-1]+1
    return start_port


def get_ips_of_instances(resource, title):
    ips_of_instances = ['']
    while '' in ips_of_instances:
        ips_of_instances = [instance.private_ip_address for instance in
           resource.instances.filter(Filters=[{'Name': 'tag:Name', 'Values': [title]}])]
    return  ips_of_instances


def run_container(name, postfix, ip,selenium_host, selenium_port,
                  version, share_path, docker_client, port, network):

    container = docker_client.containers.run(DOCKER_IMAGE_NAME,privileged=True, volumes={share_path:share_path},
                                      ports={4723: port},network=network,name = name+postfix+'_'+str(port),
                                      detach=True,environment={"CONNECT_TO_GRID":"True",
                                                               "APPIUM_HOST":name+postfix+'_'+str(port),
                                                   "APPIUM_PORT": 4723,
                                                   "SELENIUM_HOST":selenium_host,
                                                   'SELENIUM_PORT':selenium_port,"OS_VERSION":version,
                                                   "DEVICE_NAME": ip+":5555"})
    connect_result=b''
    while 'connected' not in connect_result.decode('utf-8'):
        connect_result = container.exec_run(cmd="adb connect "+ip+":5555")
    container.exec_run(cmd="adb shell setprop genyd.gps.status enabled")

def delete_containers(name, postfix):
    client = docker.from_env()
    containers = client.containers.list(filters={"ancestor": DOCKER_IMAGE_NAME})
    for container in containers:
        if container.name.split("_")[0]==name+postfix:
            Thread(target=delete_container, args=[container]).start()


def delete_container(container):
    container.exec_run(cmd="adb disconnect")
    container.stop()
    container.remove()


def parse_options():
    parser = argparse.ArgumentParser()

    parser.add_argument("-m", "--method", help="Start or Stop instances", dest="method")

    parser.add_argument("-r", "--region", help="aws region", dest="region")
    parser.add_argument("--secret_key", help="aws secret key", dest="secret_key")
    parser.add_argument("--secret_id", help="aws secret id", dest="secret_id")

    parser.add_argument("--genymotion_ami_id", help="id of image for genymotion", dest="genymotion_ami_id")
    parser.add_argument("-t", "--type_of_instance", help="type os aws instance for genymotion", dest="type_of_instance")
    parser.add_argument("-k", "--key_name", help="name of ssh key for instance", dest="key_name")
    parser.add_argument("--security_group_id", help="Id of security group for genymotion instance",
                      dest="security_group_id")
    parser.add_argument("--subnet_id", help="Id of subnet for genymotion instance", dest="subnet_id")

    parser.add_argument("--number", help="Number of instances to run", dest="number")

    parser.add_argument("-v", "--version", help="Version of running instances", dest="version")
    parser.add_argument("-n", "--name", help="Base name for instances", dest="name")
    parser.add_argument("-p", "--postfix", help="Additional name for instances", dest="postfix")
    parser.add_argument("--volume_path", help="Path to workdir for sharing with container", dest="volume_path")
    parser.add_argument("--selenium_host", help="Host of selenium grid", dest="selenium_host")
    parser.add_argument("--selenium_port", help="Port of selenium grid", dest="selenium_port")
    parser.add_argument("--network", help="Network of selenium grid", dest="network")

    args = parser.parse_args()
    if  args.method == 'start':
        create(args.name, args.postfix, args.type_of_instance, args.genymotion_ami_id, int(args.number), args.region,
               args.key_name, args.security_group_id, args.subnet_id, args.secret_id, args.secret_key,
               args.selenium_host, args.selenium_port, args.version, args.volume_path, args.network)
    elif args.method == 'stop':
        stop(args.name,args.postfix,args.region, args.secret_id, args.secret_key)


if __name__ == "__main__":
    parse_options()

