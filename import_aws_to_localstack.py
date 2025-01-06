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
from dotenv import load_dotenv

load_dotenv()

LIST_COMMANDS = {
    "s3": "aws s3api list-buckets",
    "ec2": "aws ec2 describe-instances",
    "lambda": "aws lambda list-functions",
    "sqs": "aws sqs list-queues",
    "rds": "aws rds describe-db-instances",
    "dynamodb": "aws dynamodb list-tables"
}

LOCALSTACK_ENDPOINT = os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566")
LOCALSTACK_S3_BUCKET = os.environ.get("LOCALSTACK_S3_BUCKET", "localstack2-bucket")

# Configure logging
logging.basicConfig(filename=os.environ.get("LOG_FILE_NAME", "clone_aws_to_localstack.log"), level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

def run_command(command):
    """Run a shell command and return the output."""
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"Error running command: {command} - {result.stderr}")
        return None, result.stderr
    return result.stdout, None

# AWS RDS master credentials
AWS_RDS_MASTER_USERNAME = os.environ.get("AWS_RDS_MASTER_USERNAME", "admin")
AWS_RDS_MASTER_PASSWORD = os.environ.get("AWS_RDS_MASTER_PASSWORD", "")

# Local MySQL credentials
LOCAL_MYSQL_HOST = os.environ.get("LOCAL_MYSQL_HOST", "localhost")
LOCAL_MYSQL_PORT = int(os.environ.get("LOCAL_MYSQL_PORT", "3306"))
LOCAL_MYSQL_USER = os.environ.get("LOCAL_MYSQL_USER", "master")
LOCAL_MYSQL_PASSWORD = os.environ.get("LOCAL_MYSQL_PASSWORD", "secret99")
LOCAL_MYSQL_DATABASE = os.environ.get("LOCAL_MYSQL_DATABASE", "mydb")

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
                # EC2 is limited in LocalStack; consider mocking or skipping detailed cloning.
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
    aws_lambda_client = boto3.client('lambda')
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
                role = "arn:aws:iam::000000000000:role/lambda-role"
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
    """
    Clone RDS instances from AWS to LocalStack, and optionally copy
    MySQL data.
    """
    output, error = run_command(LIST_COMMANDS["rds"])
    if error:
        print(f"Failed to describe RDS instances: {error}")
        return
    if not output:
        return

    try:
        rds_data = json.loads(output)
        db_instances = rds_data.get("DBInstances", [])
        for db_instance in tqdm(db_instances, desc="Cloning RDS instances"):
            db_instance_id = db_instance.get("DBInstanceIdentifier")
            if not db_instance_id:
                continue
            if filter_name and filter_name not in db_instance_id:
                continue

            # Gather what I need
            db_instance_class = db_instance.get("DBInstanceClass", "db.t2.micro")
            engine = db_instance.get("Engine", "mysql").lower()

            # Create the DB in LocalStack
            print(f"Creating RDS instance '{db_instance_id}' in LocalStack ...")
            create_db_command = (
                f"aws --endpoint-url={LOCALSTACK_ENDPOINT} rds create-db-instance "
                f"--db-instance-identifier {db_instance_id} "
                f"--db-instance-class {db_instance_class} "
                f"--engine {engine} "
                f"--master-username {LOCAL_MYSQL_USER} "
                f"--master-user-password {LOCAL_MYSQL_PASSWORD} "
            )
            _, create_err = run_command(create_db_command)
            if create_err:
                print(f"Failed to create RDS instance '{db_instance_id}': {create_err}")
                continue

            # If MySQL/Aurora ask to copy data from AWS
            if engine in ["mysql", "aurora-mysql"]:
                copy_data_input = input(
                    f"Do you want to copy actual data from AWS RDS instance '{db_instance_id}' to LocalStack? (y/n): "
                ).strip().lower()

                if not copy_data_input.startswith("y"):
                    print(f"Skipping data copy for RDS instance '{db_instance_id}'.")
                    continue

                print(f"Attempting to copy data from AWS RDS '{db_instance_id}' ...")

                endpoint_info = db_instance.get("Endpoint")
                if not endpoint_info:
                    print(f"No endpoint found for '{db_instance_id}'. Skipping data copy.")
                    continue

                aws_rds_host = endpoint_info["Address"]
                aws_rds_port = endpoint_info["Port"]

                # Try to get the AWS master username from the instance, or fallback to env
                aws_rds_user = db_instance.get("MasterUsername", AWS_RDS_MASTER_USERNAME)
                aws_rds_db_name = db_instance.get("DBName")
                if not aws_rds_db_name:
                    print(f"No 'DBName' found for '{db_instance_id}'. Skipping data copy.")
                    continue

                # Use the .env password for the real AWS RDS Password
                if not AWS_RDS_MASTER_PASSWORD:
                    print(
                        "Warning: AWS_RDS_MASTER_PASSWORD not found in .env. "
                        f"Cannot connect to AWS RDS '{db_instance_id}'. Skipping data copy."
                    )
                    continue

                dump_file = f"/tmp/{db_instance_id}.sql"

                # Dump from AWS RDS (requires mysqldump)
                dump_cmd = (
                    f"mysqldump -h {aws_rds_host} -P {aws_rds_port} "
                    f"-u {aws_rds_user} -p'{AWS_RDS_MASTER_PASSWORD}' {aws_rds_db_name} "
                    f"> {dump_file}"
                )
                print(f"Dumping data from AWS RDS with command:\n  {dump_cmd}")
                _, dump_err = run_command(dump_cmd)
                if dump_err:
                    print(f"mysqldump failed: {dump_err}")
                    continue

                # Wait for Local MySQL to become available
                print("Waiting for MySQL to become ready (sleep 20s) ...")
                time.sleep(20)  # @todo make this better

                # Import into Local MySQL
                import_cmd = (
                    f"mysql -h {LOCAL_MYSQL_HOST} -P {LOCAL_MYSQL_PORT} "
                    f"-u {LOCAL_MYSQL_USER} -p'{LOCAL_MYSQL_PASSWORD}' {LOCAL_MYSQL_DATABASE} "
                    f"< {dump_file}"
                )
                print(f"Importing data into mysql with command:\n  {import_cmd}")
                _, import_err = run_command(import_cmd)
                if import_err:
                    print(f"Data import failed: {import_err}")
                else:
                    print(f"Successfully imported data into LocalStack RDS for '{db_instance_id}'.")

                # Clean up dump file
                if os.path.exists(dump_file):
                    os.remove(dump_file)
            else:
                print(
                    f"Engine '{engine}' is not recognized as MySQL. "
                    "Skipping data import."
                )

    except KeyError as e:
        logger.error(f"KeyError while parsing RDS output: {e}")
        print(f"KeyError while parsing RDS output: {e}")
    except Exception as e:
        logger.error(f"Error cloning RDS instances: {e}")
        print(f"Error cloning RDS instances: {e}")

def clone_dynamodb_tables(filter_name=None):
    """Clone DynamoDB tables from AWS to LocalStack."""

    aws_dynamodb_client = boto3.client('dynamodb')
    local_dynamodb_client = boto3.client('dynamodb', endpoint_url=LOCALSTACK_ENDPOINT)

    output, error = run_command(LIST_COMMANDS["dynamodb"])
    if error:
        print(f"Failed to list DynamoDB tables: {error}")
        return
    if output:
        tables = json.loads(output).get("TableNames", [])
        for table_name in tqdm(tables, desc="Cloning DynamoDB tables"):
            if filter_name and filter_name not in table_name:
                continue
            try:
                desc = aws_dynamodb_client.describe_table(TableName=table_name)
                table_def = desc["Table"]
                key_schema = table_def["KeySchema"]
                attribute_definitions = table_def["AttributeDefinitions"]
                global_secondary_indexes = table_def.get("GlobalSecondaryIndexes", [])
                local_secondary_indexes = table_def.get("LocalSecondaryIndexes", [])

                # Check billing mode
                billing_mode = table_def.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED")

                create_params = {
                    "TableName": table_name,
                    "KeySchema": key_schema,
                    "AttributeDefinitions": attribute_definitions,
                }

                # For GSI/LSI, we still need to specify them even if PAY_PER_REQUEST
                if global_secondary_indexes:
                    # For on-demand tables, GSIs should also be on-demand (no provisioned throughput).
                    # If original table is on-demand, just remove throughput from GSIs
                    if billing_mode == "PAY_PER_REQUEST":
                        create_params["GlobalSecondaryIndexes"] = [
                            {
                                "IndexName": gsi["IndexName"],
                                "KeySchema": gsi["KeySchema"],
                                "Projection": gsi["Projection"]
                            } for gsi in global_secondary_indexes
                        ]
                    else:
                        create_params["GlobalSecondaryIndexes"] = [
                            {
                                "IndexName": gsi["IndexName"],
                                "KeySchema": gsi["KeySchema"],
                                "Projection": gsi["Projection"],
                                "ProvisionedThroughput": {
                                    "ReadCapacityUnits": max(1, gsi["ProvisionedThroughput"]["ReadCapacityUnits"]),
                                    "WriteCapacityUnits": max(1, gsi["ProvisionedThroughput"]["WriteCapacityUnits"])
                                }
                            } for gsi in global_secondary_indexes
                        ]

                if local_secondary_indexes:
                    # LSI does not allow changing billing mode but doesn't require throughput specification.
                    create_params["LocalSecondaryIndexes"] = [
                        {
                            "IndexName": lsi["IndexName"],
                            "KeySchema": lsi["KeySchema"],
                            "Projection": lsi["Projection"]
                        } for lsi in local_secondary_indexes
                    ]

                if billing_mode == "PAY_PER_REQUEST":
                    create_params["BillingMode"] = "PAY_PER_REQUEST"
                else:
                    # If provisioned, ensure throughput values are at least 1
                    provisioned_throughput = table_def["ProvisionedThroughput"]
                    create_params["ProvisionedThroughput"] = {
                        "ReadCapacityUnits": max(1, provisioned_throughput["ReadCapacityUnits"]),
                        "WriteCapacityUnits": max(1, provisioned_throughput["WriteCapacityUnits"])
                    }

                # Attempt to create the table in LocalStack
                try:
                    local_dynamodb_client.create_table(**create_params)
                    local_dynamodb_client.get_waiter('table_exists').wait(TableName=table_name)
                    print(f"Created DynamoDB table '{table_name}' in LocalStack.")
                except local_dynamodb_client.exceptions.ResourceInUseException:
                    print(f"Table '{table_name}' already exists in LocalStack, skipping creation.")

                # Copy data from AWS to LocalStack
                paginator = aws_dynamodb_client.get_paginator('scan')
                items_to_copy = []
                for page in paginator.paginate(TableName=table_name):
                    items = page.get("Items", [])
                    for item in items:
                        items_to_copy.append({"PutRequest": {"Item": item}})
                        if len(items_to_copy) == 25:
                            local_dynamodb_client.batch_write_item(RequestItems={table_name: items_to_copy})
                            items_to_copy = []
                if items_to_copy:
                    local_dynamodb_client.batch_write_item(RequestItems={table_name: items_to_copy})

                print(f"Successfully cloned DynamoDB table '{table_name}' with data into LocalStack.")

            except aws_dynamodb_client.exceptions.ResourceNotFoundException:
                print(f"Source DynamoDB table '{table_name}' not found. Skipping.")
            except Exception as e:
                logger.error(f"Error cloning DynamoDB table '{table_name}': {e}")
                print(f"Error cloning DynamoDB table '{table_name}': {e}")


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
    print("2. Clone specific services (s3, ec2, lambda, sqs, cognito, rds, dynamodb)")

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
            "rds": clone_rds_instances,
            "dynamodb": clone_dynamodb_tables
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
                            help='List of specific services to clone (s3, ec2, lambda, sqs, cognito, rds, dynamodb)')

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
                    "Enter services to clone (s3, ec2, lambda, sqs, cognito, rds, dynamodb) separated by spaces: ").strip().split()
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
