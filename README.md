# iac-pulumi

## Prerequisites
- Install [Pulumi](https://www.pulumi.com/docs/install/)
- Configure [AWS CLI](https://aws.amazon.com/cli/)
  
## Generate a Certificate Signing Request (CSR)
1. Install OpenSSL on macOS
```
brew install openssl
```
2. Generate a Private Key
```
openssl genrsa -out demo.shuolin.me.key 2048
```
3. Generate the CSR
```
openssl req -new -key demo.shuolin.me.key -out demo.shuolin.me.csr
```
4. Complete Validation Process
add cname record with host and target to aws route 53
download and receieve the certificate files ending with .crt and .key in demo_shuolin.me.zip

5. Import the Certificate into AWS Certificate Manager
Attach AWSCertificateManagerFullAccess policy to demo user
```
aws acm import-certificate --certificate fileb://demo_shuolin_me.crt --private-key fileb://demo.shuolin.me.key --certificate-chain fileb://demo_shuolin_me.ca-bundle
```
This command outputs the CertificateArn, which can be imported in load balancer listener 

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