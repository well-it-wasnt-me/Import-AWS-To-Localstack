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
    "sqs": "aws sqs list-queues",
    "rds": "aws rds describe-db-instances"
}

LOCALSTACK_ENDPOINT = "http://localhost:4566"
LOCALSTACK_S3_BUCKET = "localstack2-bucket"

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
                # LocalStack EC2 is limited. mockup to be created here.
                pass

def ensure_bucket_exists(bucket_name, s3_client):
    """Ensure the specified S3 bucket exists in LocalStack."""
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        print(f"Bucket '{bucket_name}' already exists.")
    except s3_client.exceptions.ClientError as e:
        error_code = e.response['Error'].get('Code', '')
        if error_code == '404':
            print(f"Bucket '{bucket_name}' does not exist. Creating it.")
            s3_client.create_bucket(Bucket=bucket_name)
        else:
            # Re-raise if it's an unexpected error
            raise


def clone_lambda_functions(filter_name=None):
    """Clone Lambda functions from AWS to LocalStack."""
    # AWS clients (for reading original Lambda functions)
    aws_lambda_client = boto3.client('lambda')

    # LocalStack clients (for creating Lambda functions)
    lambda_client = boto3.client('lambda', endpoint_url=LOCALSTACK_ENDPOINT)
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

            local_code_path = None

            # Fetch additional details for the function from AWS
            try:
                details = aws_lambda_client.get_function(FunctionName=function_name)
                runtime = function["Runtime"]
                # Use a dummy role ARN compatible with LocalStack
                # (LocalStack does not validate IAM roles strictly)
                role = "arn:aws:iam::000000000000:role/lambda-role"
                handler = function["Handler"]
                code_url = details["Code"]["Location"]

                # Download the Lambda code from AWS
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
                # Note: LocalStack supports a limited set of runtimes. Ensure that the runtime is supported
                # or switch to a known supported runtime like "python3.9" if needed.
                response = lambda_client.create_function(
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

                if response['ResponseMetadata']['HTTPStatusCode'] not in [200, 201]:
                    print(f"Failed to create Lambda function '{function_name}' in LocalStack")
                else:
                    print(f"Successfully cloned Lambda function '{function_name}' into LocalStack.")

            except KeyError as e:
                logger.error(f"KeyError: {e} in function details: {details}")
                print(f"KeyError: {e} in function details: {details}")
            except aws_lambda_client.exceptions.ClientError as e:
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

            # Create User Pool in LocalStack (LocalStack support might be limited)
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

def clone_rds_instances(filter_name=None):
    """Clone RDS instances from AWS to LocalStack."""
    output, error = run_command(LIST_COMMANDS["rds"])
    if error:
        print(f"Failed to describe RDS instances: {error}")
        return
    if output:
        try:
            rds_data = json.loads(output)
            db_instances = rds_data.get("DBInstances", [])
            for db_instance in tqdm(db_instances, desc="Cloning RDS instances"):
                db_instance_id = db_instance.get("DBInstanceIdentifier")
                if not db_instance_id:
                    continue
                if filter_name and filter_name not in db_instance_id:
                    continue

                # Extract minimal parameters
                db_instance_class = db_instance.get("DBInstanceClass", "db.t2.micro")
                engine = db_instance.get("Engine", "mysql")

                # Ideally you'd extract more parameters from the DB instance and replicate them.
                # For demonstration:
                create_db_command = (
                    f"aws --endpoint-url={LOCALSTACK_ENDPOINT} rds create-db-instance "
                    f"--db-instance-identifier {db_instance_id} "
                    f"--db-instance-class {db_instance_class} "
                    f"--engine {engine} "
                    "--master-username master --master-user-password secret99 "
                )

                _, error = run_command(create_db_command)
                if error:
                    print(f"Failed to create RDS instance '{db_instance_id}': {error}")
        except KeyError as e:
            logger.error(f"KeyError while parsing RDS output: {e}")
            print(f"KeyError while parsing RDS output: {e}")
        except Exception as e:
            logger.error(f"Error cloning RDS instances: {e}")
            print(f"Error cloning RDS instances: {e}")

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
    print("2. Clone specific services (s3, ec2, lambda, sqs, cognito, rds)")

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
            "cognito": clone_cognito_user_pools,
            "rds": clone_rds_instances
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
                            help='List of specific services to clone (s3, ec2, lambda, sqs, cognito, rds)')

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
                    "Enter services to clone (s3, ec2, lambda, sqs, cognito, rds) separated by spaces: ").strip().split()
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
