"""An AWS Python Pulumi program"""

import pulumi
import json
import pulumi_aws as aws

config = pulumi.Config()

# Fetch the config values
prefix = config.require("prefix")
cidrBlock = config.require("cidrBlock")
numOfSubnets = config.require_int("numOfSubnets")
cidr_first_two_octets = cidrBlock.split('.')[0] + '.' + cidrBlock.split('.')[1]
cidr_prefix_length = config.require_int("cidrPrefixLength")
subnetRegion = config.require("subnetRegion")
sourceAMI = config.require("sourceAMI")
instanceType = config.require("instanceType")
sshName = config.require("sshName")
volumeSize = config.require("volumeSize")
volumeType = config.require("volumeType")

# Define user data script
def create_user_data_script(db_endpoint):
    return db_endpoint.apply(lambda endpoint: f"""#!/bin/bash
sudo groupadd csye6225
sudo useradd -s /bin/false -g csye6225 -d /opt/csye6225 -m csye6225
sudo mv /home/admin/webapp /opt/csye6225
cd /opt/csye6225/webapp
cat <<EOL > .env
DB_NAME=csye6225
DB_USER=csye6225
DB_PASSWORD=12345678
DB_HOST={endpoint.split(":")[0]}
USER_CSV_PATH=/opt/csye6225/webapp/users.csv
EOL
sudo chown -R csye6225:csye6225 /opt/csye6225/webapp
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s -c file:/opt/cloudwatch-config.json
sudo systemctl daemon-reload
sudo systemctl restart amazon-cloudwatch-agent
sudo /usr/bin/node /opt/csye6225/webapp/index.js
sleep 10
sudo systemctl daemon-reload
sudo systemctl enable webapp.service
sudo systemctl start webapp.service
""")

# Create a vpc
vpc = aws.ec2.Vpc(
    f"{prefix}-vpc",
    cidr_block=cidrBlock,
    tags={
        "Name": f"{prefix}-vpc",
    })

# Create an internet gateway
igw = aws.ec2.InternetGateway(
    f"{prefix}-igw",
    vpc_id=vpc.id,
    tags={
        "Name": f"{prefix}-igw",
    })

# Create numOfSubnets public and private subnets
public_subnets = []
private_subnets = []
azs = aws.get_availability_zones(state="available")
max_azs = len(azs.names)
# Don't allow numOfSubnets exceed the max available zone in the current region
numOfSubnets = min(max_azs, numOfSubnets)
for i in range(numOfSubnets):
    az = f"{subnetRegion}{chr(97+i)}"  # This will give us '{us-east-1}{a}', '{us-east-1}{b}', '{us-east-1}{c} ....'
    public_subnet = aws.ec2.Subnet(
        f"{prefix}-public-subnet-{az}",
        vpc_id=vpc.id,
        cidr_block=f"{cidr_first_two_octets}.{i*2}.0/{cidr_prefix_length}", # 这算不算hard code?
        availability_zone=az,
        map_public_ip_on_launch=True, 
        tags={
            "Name": f"{prefix}-public-subnet-{az}",
        }
    )
    public_subnets.append(public_subnet)
    
    private_subnet = aws.ec2.Subnet(
        f"{prefix}-private-subnet-{az}",
        vpc_id=vpc.id,
        cidr_block=f"{cidr_first_two_octets}.{i*2+1}.0/{cidr_prefix_length}",
        availability_zone=az,
        tags={
            "Name": f"{prefix}-private-subnet-{az}",
        }
    )
    private_subnets.append(private_subnet)

# Create public route table
public_rt = aws.ec2.RouteTable(
    f"{prefix}-public-rt",
    vpc_id=vpc.id,
    tags={
        "Name": f"{prefix}-public-rt",
    })

# # Associate public subnets with the public route table
for index, subnet in enumerate(public_subnets):
    aws.ec2.RouteTableAssociation(
        f"{prefix}-public-rta-{index}",
        subnet_id=subnet.id,
        route_table_id=public_rt.id,
    )

# Create private route table
private_rt = aws.ec2.RouteTable(
    f"{prefix}-private-rt",
    vpc_id=vpc.id,
    tags={
        "Name": f"{prefix}-private-rt",
    })

# Associate private subnets with the private route table
for index, subnet in enumerate(private_subnets):
    aws.ec2.RouteTableAssociation(
        f"{prefix}-private-rta-{index}",
        subnet_id=subnet.id,
        route_table_id=private_rt.id,
    )

# Create public route
aws.ec2.Route("public-route",
    route_table_id=public_rt.id,
    destination_cidr_block="0.0.0.0/0",
    gateway_id=igw.id,
)

# Create an application security group for EC2
app_sg = aws.ec2.SecurityGroup('app-sg',
    vpc_id=vpc.id,
    description='Application security group',
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            protocol='tcp',
            from_port=22,
            to_port=22,
            cidr_blocks=["0.0.0.0/0"]
        ),
        aws.ec2.SecurityGroupIngressArgs(
            protocol='tcp',
            from_port=80,
            to_port=80,
            cidr_blocks=["0.0.0.0/0"]
        ),
        aws.ec2.SecurityGroupIngressArgs(
            protocol='tcp',
            from_port=443,
            to_port=443,
            cidr_blocks=["0.0.0.0/0"]
        ),
        aws.ec2.SecurityGroupIngressArgs(
            protocol='tcp',
            from_port=8080,
            to_port=8080,
            cidr_blocks=["0.0.0.0/0"]
        ),  
    ],
    egress=[aws.ec2.SecurityGroupEgressArgs(
        from_port=0,
        to_port=0,
        protocol="-1",
        cidr_blocks=["0.0.0.0/0"]
    )]
)

# Create a database security group for EC2
db_sg = aws.ec2.SecurityGroup('db-security-group',
    vpc_id=vpc.id,
    description='Security group for RDS database instances',
    egress=[aws.ec2.SecurityGroupEgressArgs(
        from_port=0,
        to_port=0,
        protocol="-1",
        cidr_blocks=["0.0.0.0/0"]
    )],
    tags={
        "Name": "Database Security Group",
    }
)

db_ingress = aws.ec2.SecurityGroupRule("db-ingress-rule",
    type="ingress",
    from_port=3306,
    to_port=3306,
    protocol="tcp",
    security_group_id=db_sg.id,
    source_security_group_id=app_sg.id)

# Create a subnet group for the rds instance
db_subnet_group = aws.rds.SubnetGroup("db-subnet-group",
    subnet_ids=[subnet.id for subnet in private_subnets]
)

# Create a parameter group for rds instance
parameter_group = aws.rds.ParameterGroup("db-parameter-group",
    family="mariadb10.6",
    description='Mariadb parameter group',
    parameters=[
        aws.rds.ParameterGroupParameterArgs(
            name="character_set_server",
            value="utf8",
        ),
    ])

# Launch an Database instance in one of the private subnets
db_instance = aws.rds.Instance("db-instance",
    instance_class="db.t3.micro",
    allocated_storage=20,
    engine="mariadb",
    engine_version="10.6.14",
    storage_type="gp2",
    db_name="csye6225",
    username="csye6225",
    password="12345678",
    multi_az=False,
    publicly_accessible=False,
    vpc_security_group_ids=[db_sg.id],
    db_subnet_group_name=db_subnet_group.name,
    parameter_group_name=parameter_group.name,
    skip_final_snapshot=True,
    tags={"Name": "db-instance"}
)

# Create IAM role for use with CloudAgent
cloud_watch_role = aws.iam.Role("CloudWatchAgentServerRole",
    assume_role_policy=json.dumps({
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "sts:AssumeRole"
            ],
            "Principal": {
                "Service": [
                    "ec2.amazonaws.com"
                ]
            }
        }
    ]
}))

# Attach policy to role
cloud_watch_role_policy_attachment = aws.iam.RolePolicyAttachment("cloud-watch-role-policy-attachment",
    role=cloud_watch_role.name,
    policy_arn="arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy")

# Create IAM instance profile for attaching role to EC2
cloud_watch_instance_profile = aws.iam.InstanceProfile("cloud-watch-instance-profile",
    role=cloud_watch_role.name)

# Launch an EC2 instance in one of the public subnets
ec2_instance = aws.ec2.Instance('app-instance',
    instance_type=instanceType,
    ami=sourceAMI,
    key_name=sshName,
    user_data=create_user_data_script(db_instance.endpoint),  
    vpc_security_group_ids=[app_sg.id],
    subnet_id=public_subnets[0].id,  # Launch in the first public subnet
    iam_instance_profile=cloud_watch_instance_profile.name,
    root_block_device=aws.ec2.InstanceRootBlockDeviceArgs(
        volume_size=volumeSize,
        volume_type=volumeType,
        delete_on_termination=True
    ),
    disable_api_termination=False,
    tags={
        "Name": "Webapp Instance",
    }
)

# Fetch the host zone dev.shuolin.me
zone = aws.route53.get_zone(name="dev.shuolin.me")

# Add or Update A record to Route53 zone
A_record = aws.route53.Record("zone-record",
    zone_id=zone.zone_id,
    name="dev.shuolin.me",
    type="A",
    ttl=300,
    records=[ec2_instance.public_ip])

# Export the RDS instance endpoint
pulumi.export('db_instance_endpoint', db_instance.endpoint)

# Export the EC2 instance public IP to easily access it after provisioning
pulumi.export('instance_public_ip', ec2_instance.public_ip)