"""An AWS Python Pulumi program"""

import base64
import pulumi
import json
import pulumi_aws as aws
import pulumi_gcp as gcp

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
dbName = config.require("dbName")
dbUser = config.require("dbUser")
dbPassword = config.require("dbPassword")
hostZone = config.require("hostZone")
gcs_bucket_name = config.require("gcs_bucket_name")
lambdaPath = config.require("lambdaPath")

##########################################################################################
##########################################################################################

accountID = config.require("accountID")
accountName = config.require("accountName")
projectID = config.require("projectID")
mailgunApi = config.require("mailgunApi")
mailgunDomain = config.require("mailgunDomain")
certificateArn = config.require("certificateArn")

# Create a Google Service Account
service_account = gcp.serviceaccount.Account("my-service-account",
    account_id=accountID,
    display_name=accountName)

# Attach Storage Object User Role to the Service Account
storage_object_user_binding = gcp.projects.IAMMember("storage-object-user-binding",
    project=projectID,
    role="roles/storage.objectUser",
    member=pulumi.Output.concat("serviceAccount:", service_account.email))

# Access Keys for the Google Service Account
service_account_key = gcp.serviceaccount.Key("my-service-account-key",
    service_account_id=service_account.name)

# IAM role for the Lambda function
lambda_role = aws.iam.Role("lambdaRole",
    assume_role_policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }))

# Attach the policy to the role
aws.iam.RolePolicyAttachment("sns-lambda-attachment",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonSNSFullAccess")

aws.iam.RolePolicyAttachment("cloudwatch-lambda-attachment",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")

aws.iam.RolePolicyAttachment("dynamodb-lambda-attachment",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess")

# Create a DynamoDB table
email_delivery_table = aws.dynamodb.Table("emailDeliveryTable",
    attributes=[
        aws.dynamodb.TableAttributeArgs(
            name="submissionId",
            type="S",
        ),
    ],
    hash_key="submissionId",
    billing_mode="PAY_PER_REQUEST"
)

# Assuming your Lambda code is zipped in 'lambda_function.zip'
lambda_function = aws.lambda_.Function("myLambdaFunction",
    role=lambda_role.arn,
    runtime="nodejs18.x",  
    handler="lambda_function.handler",
    timeout=60,
    code=pulumi.FileArchive(lambdaPath),
    environment=aws.lambda_.FunctionEnvironmentArgs(
        variables={
            "GCS_BUCKET_NAME": gcs_bucket_name,
            "GCP_SERVICE_ACCOUNT_KEY_JSON": service_account_key.private_key.apply(
                lambda key: base64.b64decode(key).decode('utf-8') if key else ''
            ),
            "MAILGUN_API_KEY": mailgunApi,
            "MAILGUN_DOMAIN": mailgunDomain,
            "EMAIL_DELIVERY_TABLE_NAME": email_delivery_table.name
        }) 
    )

# Create an AWS resource (SNS Topic)
sns_topic = aws.sns.Topic('myTopic')

# Configure SNS Topic to Trigger Lambda
sns_topic_subscription = aws.sns.TopicSubscription("myTopicSubscription",
    topic=sns_topic.arn,
    protocol="lambda",
    endpoint=lambda_function.arn)

# Add Lambda permission for SNS to invoke it
lambda_invoke_permission = aws.lambda_.Permission("snsInvokePermission",
    action="lambda:InvokeFunction",
    function=lambda_function.name,
    principal="sns.amazonaws.com",
    source_arn=sns_topic.arn)


pulumi.export('sns topic arn', sns_topic.arn)

##########################################################################################
##########################################################################################

# Define user data script
def create_user_data_script(endpoint, topic_arn):
    return f"""#!/bin/bash
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
TOPIC_ARN={topic_arn}
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
"""

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

# Create an load balancer security group
load_balancer_sg = aws.ec2.SecurityGroup('load-balancer-sg',
    vpc_id=vpc.id,         
    ingress=[
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
        )       
    ],      
    egress=[aws.ec2.SecurityGroupEgressArgs(
        from_port=0,
        to_port=0,
        protocol="-1",
        cidr_blocks=["0.0.0.0/0"]
    )]
    )

# Create an application security group
app_sg = aws.ec2.SecurityGroup('app-sg',
    vpc_id=vpc.id,
    description='Allow on port 22',
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=8080,
            to_port=8080,
            security_groups=[load_balancer_sg.id]
        ),
    ],
    egress=[aws.ec2.SecurityGroupEgressArgs(
        from_port=0,
        to_port=0,
        protocol="-1",
        cidr_blocks=["0.0.0.0/0"]
    )]
)

# Create a database security group
db_sg = aws.ec2.SecurityGroup('db-security-group',
    vpc_id=vpc.id,
    description='Security group for RDS database instances',
    egress=[aws.ec2.SecurityGroupEgressArgs(
        from_port=0,
        to_port=0,
        protocol="-1",
        cidr_blocks=["0.0.0.0/0"]
    )]
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
    db_name=dbName,
    username=dbUser,
    password=dbPassword,
    multi_az=False,
    publicly_accessible=False,
    vpc_security_group_ids=[db_sg.id],
    db_subnet_group_name=db_subnet_group.name,
    parameter_group_name=parameter_group.name,
    skip_final_snapshot=True,
    tags={"Name": "db-instance"}
)

# Create IAM role for use with CloudAgent
iam_role = aws.iam.Role("iam_role",
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

# Attach cloud_watch policy to iam role
cloud_watch_role_policy_attachment = aws.iam.RolePolicyAttachment("cloud-watch-role-policy-attachment",
    role=iam_role.name,
    policy_arn="arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy")

# Attach Amazon SNS Full Access policy to the role
sns_full_access_policy_attachment = aws.iam.RolePolicyAttachment("sns-full-access-policy-attachment",
    role=iam_role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonSNSFullAccess")

# Create IAM instance profile for attaching role to EC2
iam_instance_profile = aws.iam.InstanceProfile("iam-instance-profile",
    role=iam_role.name)

# The Auto Scaling Application Stack
# Create the launch template
launch_template = aws.ec2.LaunchTemplate("app-launch-template",
    image_id=sourceAMI,
    instance_type=instanceType,
    key_name=sshName,
    disable_api_termination=False,
    user_data=pulumi.Output.all(db_instance.endpoint, sns_topic.arn).apply(
        lambda args: base64.b64encode(create_user_data_script(args[0], args[1]).encode('utf-8')).decode('utf-8')
    ),
    iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(
        name=iam_instance_profile.name,
    ), 
    network_interfaces=[aws.ec2.LaunchTemplateNetworkInterfaceArgs(
        associate_public_ip_address="true",
        security_groups=[app_sg.id],
    )]
)

# Create auto scaling group
auto_scaling_group = aws.autoscaling.Group("app-auto-scaling-group",
    launch_template=aws.autoscaling.GroupLaunchTemplateArgs(
        id=launch_template.id,
        version="$Latest",
    ),
    vpc_zone_identifiers=[subnet.id for subnet in public_subnets], 
    min_size=1,
    max_size=3,
    desired_capacity=1,
    default_cooldown=60,
    tags=[aws.autoscaling.GroupTagArgs(
        key="Name",
        value="web-app-instance",
        propagate_at_launch=True,
    )]
)

# Scale Up Policy
scale_up_policy = aws.autoscaling.Policy("scale-up-policy",
    scaling_adjustment=1,
    adjustment_type="ChangeInCapacity",
    cooldown=60,
    autoscaling_group_name=auto_scaling_group.name,
    metric_aggregation_type="Average",
    policy_type="SimpleScaling"
)

# CloudWatch Metric Alarm for Scale Up
scale_up_alarm = aws.cloudwatch.MetricAlarm("scale-up-alarm",
    metric_name="CPUUtilization",
    namespace="AWS/EC2",
    statistic="Average",
    comparison_operator="GreaterThanThreshold",
    threshold=5,
    period=60,
    evaluation_periods=2,
    alarm_description="CPU Utilization exceeds 5%",
    alarm_actions=[scale_up_policy.arn],
    dimensions={"AutoScalingGroupName": auto_scaling_group.name}
)

# Scale Down Policy
scale_down_policy = aws.autoscaling.Policy("scale-down-policy",
    scaling_adjustment=-1,
    adjustment_type="ChangeInCapacity",
    cooldown=60,
    autoscaling_group_name=auto_scaling_group.name,
    metric_aggregation_type="Average",
    policy_type="SimpleScaling"
)

# CloudWatch Metric Alarm for Scale Down
scale_down_alarm = aws.cloudwatch.MetricAlarm("scale-down-alarm",
    metric_name="CPUUtilization",
    namespace="AWS/EC2",
    statistic="Average",
    comparison_operator="LessThanThreshold",
    threshold=3,
    period=60,
    evaluation_periods=2,
    alarm_description="CPU Utilization below 3%",
    alarm_actions=[scale_down_policy.arn],
    dimensions={"AutoScalingGroupName": auto_scaling_group.name}
)

# Setup Application Load Balancer For Your Web Application
# Define the load balancer
app_load_balancer = aws.lb.LoadBalancer("appLoadBalancer",
    internal=False,
    load_balancer_type="application",
    security_groups=[load_balancer_sg.id],
    subnets=[subnet.id for subnet in public_subnets],
    tags={
        "Name": "appLoadBalancer"
    }
)

# Create a target group
app_target_group = aws.lb.TargetGroup("appTargetGroup",
    port=8080,
    protocol="HTTP",
    vpc_id=vpc.id,
    target_type="instance",
    health_check={
        "interval": 30,
        "path": "/healthz", 
        "protocol": "HTTP",
        "timeout": 3,
        "healthy_threshold": 3,
        "unhealthy_threshold": 3
    },
    tags={
        "Name": "appTargetGroup"
    }
)

# Create a listener
app_listener = aws.lb.Listener("appListener",
    load_balancer_arn=app_load_balancer.arn,
    port=443,
    protocol="HTTPS",
    ssl_policy="ELBSecurityPolicy-2016-08",
    certificate_arn=certificateArn,
    default_actions=[{
        "type": "forward",
        "target_group_arn": app_target_group.arn
    }]
)

# Attachment of the Auto Scaling Group to the Target Group
asg_attachment = aws.autoscaling.Attachment("asg-attachment",
    autoscaling_group_name=auto_scaling_group.name,
    lb_target_group_arn=app_target_group.arn
)

# Fetch the host zone dev.shuolin.me
zone = aws.route53.get_zone(name=hostZone)

# Add or Update A record to Route53 zone
a_record = aws.route53.Record("zone-record",
    zone_id=zone.zone_id,
    name=hostZone,
    type="A",
    aliases=[
        aws.route53.RecordAliasArgs(
            name=app_load_balancer.dns_name,
            zone_id=app_load_balancer.zone_id,
            evaluate_target_health=True
        )
    ]
)

# Export the RDS instance endpoint
pulumi.export('db_instance_endpoint', db_instance.endpoint)

# Export the EC2 instance public IP to easily access it after provisioning
pulumi.export('app_url', app_load_balancer.dns_name)
