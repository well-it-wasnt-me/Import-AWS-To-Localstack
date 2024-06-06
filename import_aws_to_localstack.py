import os
import subprocess
import json
import boto3
import time
import argparse
import logging
from tqdm import tqdm
from pyfiglet import Figlet
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

LIST_COMMANDS = {
    "s3": "aws s3api list-buckets",
    "ec2": "aws ec2 describe-instances",
    "lambda": "aws lambda list-functions",
    "sqs": "aws sqs list-queues"
}

LOCALSTACK_ENDPOINT = "http://localhost:4566"
LOCALSTACK_S3_BUCKET = "localstack-bucket"

# Configure logging
logging.basicConfig(filename='clone_aws_to_localstack.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

def run_command(command):
    """Run a shell command and return the output."""
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"Error running command: {command} - {result.stderr}")
        return None, result.stderr
    return result.stdout, None

def clone_s3_buckets(filter_name=None):
    """Clone S3 buckets from AWS to LocalStack."""
    output, error = run_command(LIST_COMMANDS["s3"])
    if error:
        print(f"Failed to list S3 buckets: {error}")
        return
    if output:
        buckets = json.loads(output)["Buckets"]
        for bucket in tqdm(buckets, desc="Cloning S3 buckets"):
            bucket_name = bucket["Name"]
            if filter_name and filter_name not in bucket_name:
                continue
            create_bucket_command = f"aws --endpoint-url={LOCALSTACK_ENDPOINT} s3api create-bucket --bucket {bucket_name}"
            _, error = run_command(create_bucket_command)
            if error:
                print(f"Failed to create S3 bucket '{bucket_name}': {error}")

def clone_ec2_instances(filter_name=None):
    """Clone EC2 instances from AWS to LocalStack."""
    output, error = run_command(LIST_COMMANDS["ec2"])
    if error:
        print(f"Failed to describe EC2 instances: {error}")
        return
    if output:
        instances = json.loads(output)["Reservations"]
        for reservation in tqdm(instances, desc="Cloning EC2 instances"):
            for instance in reservation["Instances"]:
                instance_name = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Name'), None)
                if filter_name and (not instance_name or filter_name not in instance_name):
                    continue
                pass  # Extract necessary instance details and create instances in LocalStack

def ensure_bucket_exists(bucket_name, s3_client):
    """Ensure the specified S3 bucket exists in LocalStack."""
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        print(f"Bucket '{bucket_name}' already exists.")
    except s3_client.exceptions.NoSuchBucket:
        print(f"Bucket '{bucket_name}' does not exist. Creating it.")
        s3_client.create_bucket(Bucket=bucket_name)

def clone_lambda_functions(filter_name=None):
    """Clone Lambda functions from AWS to LocalStack."""
    client = boto3.client('lambda')
    s3_client = boto3.client('s3', endpoint_url=LOCALSTACK_ENDPOINT)
    ensure_bucket_exists(LOCALSTACK_S3_BUCKET, s3_client)
    output, error = run_command(LIST_COMMANDS["lambda"])
    if error:
        print(f"Failed to list Lambda functions: {error}")
        return
    if output:
        functions = json.loads(output).get("Functions", [])
        for function in tqdm(functions, desc="Cloning Lambda functions"):
            function_name = function.get("FunctionName")
            if not function_name:
                logger.error(f"Function without a name found: {function}")
                continue
            if filter_name and filter_name not in function_name:
                continue

            local_code_path = None  # Initialize local_code_path here

            # Fetch additional details for the function
            try:
                details = client.get_function(FunctionName=function_name)
                runtime = function["Runtime"]
                role = function["Role"]
                handler = function["Handler"]
                code_url = details["Code"]["Location"]

                # Download the Lambda code
                response = requests.get(code_url)
                if response.status_code != 200:
                    logger.error(f"Failed to download code for Lambda function '{function_name}'")
                    continue

                local_code_path = f"/tmp/{function_name}.zip"
                with open(local_code_path, 'wb') as f:
                    f.write(response.content)

                # Upload the code to LocalStack S3
                s3_key = f"{function_name}.zip"
                s3_client.upload_file(local_code_path, LOCALSTACK_S3_BUCKET, s3_key)

                # Create the Lambda function in LocalStack
                response = client.create_function(
                    FunctionName=function_name,
                    Runtime=runtime,
                    Role=role,
                    Handler=handler,
                    Code={
                        'S3Bucket': LOCALSTACK_S3_BUCKET,
                        'S3Key': s3_key
                    },
                    Publish=True
                )
                if response['ResponseMetadata']['HTTPStatusCode'] != 201:
                    print(f"Failed to create Lambda function '{function_name}'")
            except KeyError as e:
                logger.error(f"KeyError: {e} in function details: {details}")
                print(f"KeyError: {e} in function details: {details}")
            except client.exceptions.ClientError as e:
                logger.error(f"ClientError: {e} in function: {function_name}")
                print(f"ClientError: {e} in function: {function_name}")
            finally:
                if local_code_path and os.path.exists(local_code_path):
                    os.remove(local_code_path)

def clone_sqs_queues(filter_name=None):
    """Clone SQS queues from AWS to LocalStack."""
    output, error = run_command(LIST_COMMANDS["sqs"])
    if error:
        print(f"Failed to list SQS queues: {error}")
        return
    if output:
        queues = json.loads(output).get("QueueUrls", [])
        for queue_url in tqdm(queues, desc="Cloning SQS queues"):
            queue_name = queue_url.split("/")[-1]
            if filter_name and filter_name not in queue_name:
                continue
            create_queue_command = f"aws --endpoint-url={LOCALSTACK_ENDPOINT} sqs create-queue --queue-name {queue_name}"
            _, error = run_command(create_queue_command)
            if error:
                print(f"Failed to create SQS queue '{queue_name}': {error}")


def clone_cognito_user_pools(filter_name=None):
    """Clone Cognito User Pools from AWS to LocalStack."""
    cognito_client = boto3.client('cognito-idp')
    try:
        user_pools = cognito_client.list_user_pools(MaxResults=60)['UserPools']
        for user_pool in tqdm(user_pools, desc="Cloning Cognito User Pools"):
            pool_name = user_pool['Name']
            if filter_name and filter_name not in pool_name:
                continue
            pool_id = user_pool['Id']
            pool_details = cognito_client.describe_user_pool(UserPoolId=pool_id)['UserPool']

            # Create User Pool in LocalStack
            create_pool_command = (
                f"aws --endpoint-url={LOCALSTACK_ENDPOINT} cognito-idp create-user-pool "
                f"--pool-name {pool_details['Name']} --policies '{json.dumps(pool_details['Policies'])}' "
                f"--auto-verified-attributes {','.join(pool_details['AutoVerifiedAttributes'])}"
            )
            _, error = run_command(create_pool_command)
            if error:
                print(f"Failed to create Cognito User Pool '{pool_details['Name']}': {error}")
    except Exception as e:
        logger.error(f"Error cloning Cognito User Pools: {e}")
        print(f"Error cloning Cognito User Pools: {e}")

def wait_for_localstack():
    """Wait for LocalStack to be ready."""
    while True:
        try:
            response = subprocess.run(
                ["aws", "--endpoint-url", LOCALSTACK_ENDPOINT, "s3api", "list-buckets"],
                capture_output=True, text=True
            )
            if response.returncode == 0:
                print("LocalStack is ready.")
                break
        except Exception as e:
            logger.warning(f"Waiting for LocalStack to be ready: {e}")
        time.sleep(5)

def print_banner():
    f = Figlet(font='chunky')
    print(f.renderText('AWS to LocalStack'))

def display_menu():
    print("Select services to clone:")
    print("1. Clone all services")
    print("2. Clone specific services (s3, ec2, lambda, sqs, cognito)")

def start_localstack():
    """Start LocalStack using Docker Compose and wait until it's ready."""
    try:
        os.system("docker-compose up -d")
        wait_for_localstack()
    except Exception as e:
        logger.error(f"Failed to start LocalStack: {e}")
        raise


def main(clone_all, filter_name=None, selected_services=None):
    try:
        start_localstack()

        services = {
            "s3": clone_s3_buckets,
            "ec2": clone_ec2_instances,
            "lambda": clone_lambda_functions,
            "sqs": clone_sqs_queues,
            "cognito": clone_cognito_user_pools
        }

        with ThreadPoolExecutor() as executor:
            futures = []
            if clone_all:
                for service in services:
                    futures.append(executor.submit(services[service], filter_name))
            else:
                for service in selected_services:
                    if service in services:
                        futures.append(executor.submit(services[service], filter_name))
                    else:
                        logger.warning(f"Unknown service: {service}")

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Error during cloning: {e}")
                    print(f"Error during cloning: {e}")

    except NoCredentialsError:
        print("AWS credentials not found.")
        logger.error("AWS credentials not found.")
    except PartialCredentialsError:
        print("Incomplete AWS credentials.")
        logger.error("Incomplete AWS credentials.")
    except Exception as e:
        print(f"An error occurred: {e}")
        logger.error(f"An error occurred: {e}")


if __name__ == "__main__":
    try:
        boto3.client('sts').get_caller_identity()  # Test AWS credentials

        parser = argparse.ArgumentParser(description='Clone AWS resources to LocalStack.')
        parser.add_argument('--all', action='store_true', help='Clone all services')
        parser.add_argument('--specific', help='Filter services by name')
        parser.add_argument('--services', nargs='+',
                            help='List of specific services to clone (s3, ec2, lambda, sqs, cognito)')

        args = parser.parse_args()

        print_banner()

        if not args.all and not args.specific and not args.services:
            display_menu()
            choice = input("Enter choice (1/2): ").strip()
            if choice == "1":
                clone_all = True
                selected_services = None
            elif choice == "2":
                selected_services = input(
                    "Enter services to clone (s3, ec2, lambda, sqs, cognito) separated by spaces: ").strip().split()
                clone_all = False
            else:
                print("Invalid choice. Exiting.")
                exit(1)
        else:
            clone_all = args.all
            selected_services = args.services

        filter_name = input("Enter the specific name to filter by (or leave blank to skip): ").strip() or None

        main(clone_all, filter_name, selected_services)

    except NoCredentialsError:
        print("AWS credentials not found.")
        logger.error("AWS credentials not found.")
    except PartialCredentialsError:
        print("Incomplete AWS credentials.")
        logger.error("Incomplete AWS credentials.")
    except Exception as e:
        print(f"An error occurred: {e}")
        logger.error(f"An error occurred: {e}")
