# AWS to LocalStack Cloner

Hey there, tech wizard! Ever wished you could just snap your fingers and have all your AWS resources cloned to LocalStack? Well, wish no more! This script is here to make your dreams come true – just like a genie, but without the three-wish limit. So sit back, relax, and let the magic happen.

<!-- TOC -->
* [AWS to LocalStack Cloner](#aws-to-localstack-cloner)
  * [Features](#features)
  * [Working on...](#working-on)
  * [Getting Started](#getting-started)
    * [Prerequisites](#prerequisites)
    * [Installation](#installation)
  * [Usage](#usage)
    * [Interactive Mode](#interactive-mode)
    * [Command Line Mode](#command-line-mode)
  * [Configuration](#configuration)
    * [Example](#example)
  * [Troubleshooting](#troubleshooting)
  * [Contributing](#contributing)
<!-- TOC -->

## Features

- **Clone S3 Buckets**: Because who wants to manually copy buckets? Not you.
- **Clone EC2 Instances**: Even the ones with weird names like `prod-ec2-007`.
- **Clone Lambda Functions**: Functions that actually do stuff, not just `hello world`.
- **Clone SQS Queues**: Get those messages flowing.
- **Clone Cognito IdP**: Get your users.
- **Clone DynamoDB and Data**: Get your Dynamo Data.
- **Clone RDS and Data**: Get your RDS Data.
- **Progress Bar**: Because watching progress bars is more satisfying than watching paint dry.
- **Logging**: In case something goes wrong, you'll know who to blame.
- **Command-Line Interface**: For those who like to type their way to success.
- **Docker Management**: Spins up LocalStack so fast, you'll think it's on caffeine.

## Working on...
[x] **ERROR** - ClientError: An error occurred (AccessDeniedException) when calling the CreateFunction operation: Your access has been denied by S3, please make sure your request credentials have permission to GetObject for localstack-bucket/file-path.ext. S3 Error Code: AccessDenied. S3 Error Message: Access Denied in function: function-full-name
[] **BOTO/AWS-CLI** I know, i know...some stuff use aws-cli other boto...planning to switch entirely towards boto soon.

## Getting Started

### Prerequisites

- Python 3.x (If you don't have this, you're probably still using dial-up)
- AWS CLI configured (because who has time for manual API calls?)
- Docker (to keep all the techy stuff contained)
- LocalStack PRO Account (for a local AWS sandbox)
- LocalStack Auth Token

### Installation

1. Clone this repository:
    ```bash
    git clone https://github.com/well-it-wasnt-me/Import-AWS-To-Localstack.git
    cd Import-AWS-To-Localstack
    ```
   
2. Copy: .env.dist to .env and update docker-compose.yml as well with your datas
   1. .env.dist to .env and update the values according to your needs
   2. docker-compose.yml-dist to docker-compose.yml and update the values according to your needs  


3. Start venv and install the dependencies:
    ```bash
    python3 -m venv localstack-env
    source localstack-env/bin/activate
    pip install -r requirements.txt
    ```

4. Make sure Docker is running. If not, give it a nudge or a kick.

## Usage

You can run this script in two fabulous ways:

### Interactive Mode

Just run the script without any arguments and follow the prompts. It's easier than getting through a TSA checkpoint.

```bash
python import_aws_to_localstack.py
```

### Command Line Mode
For those who love command-line arguments more than coffee:

- Clone EVERYTHING

```bash
python import_aws_to_localstack.py
```

- Clone EVERYTHING but filter by name and service
```bash
python import_aws_to_localstack.py --services s3 lambda --specific "prod-"
```

## Configuration
Want to filter resources by name? Just provide the --specific argument and type away. It'll filter like a pro.

### Example

Here's how to clone all your AWS resources containing the name "dev":

```bash
python import_aws_to_localstack.py --all --specific "dev"
```

Sit back, watch the progress bars, and enjoy your favorite beverage while the script does its magic.

## Troubleshooting
- **AWS Credentials Error**: Make sure your AWS CLI is configured. If it's not, it might be time to call tech support.
- **Docker Not Running**: Start Docker. Seriously, it's not going to start itself.
- **Other Errors**: Check the logs. They might be more interesting than you think.

## Contributing
Want to contribute? Fork this repo and make a pull request