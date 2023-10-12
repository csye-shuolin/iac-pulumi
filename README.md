# iac-pulumi

## Prerequisites
- Install [Pulumi](https://www.pulumi.com/docs/install/)
- Configure [AWS CLI](https://aws.amazon.com/cli/)

## Configuration
1. Create a dev stack:
```
pulumi stack init <stack-name>
pulumi stack select <stack-name>
```
2. Configure AWS:
```
pulumi config set aws:profile <profile-name> 
pulumi config set aws:region <desired-region> 
```
3. Configure variables in Pulumi.stack-name.yaml

## Deployment
1. Preview the deployment:
```
pulumi preview
```
2. Deploy the infrastructure:
```
pulumi up
```

## Destroying Infrastructure
Tear down the deployed infrastructure
```
pulumi destroy
```