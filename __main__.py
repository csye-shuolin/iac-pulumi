"""An AWS Python Pulumi program"""

import pulumi
import pulumi_aws as aws

config = pulumi.Config()

# Fetch the config values
prefix = config.require("prefix")
cidrBlock = config.require("cidrBlock")
numOfSubnets = config.require_int("numOfSubnets")
cidr_first_two_octets = cidrBlock.split('.')[0] + '.' + cidrBlock.split('.')[1]
cidr_prefix_length = config.require_int("cidrPrefixLength")
subnetRegion = config.require("subnetRegion")

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

# Attach the internet gateway with the vpc
internet_gateway_attachment = aws.ec2.InternetGatewayAttachment(
    f"{prefix}-igw-attachment",
    internet_gateway_id=igw.id,
    vpc_id=vpc.id)

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
aws.ec2.Route(
    "public-route",
    route_table_id=public_rt.id,
    destination_cidr_block="0.0.0.0/0",
    gateway_id=igw.id,
)

# # Export the name of the bucket
# pulumi.export('bucket_name', bucket.id)
