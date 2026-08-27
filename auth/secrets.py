import json

import boto3

SECRET_ID = "rag-email-agent/token-encryption-key"
REGION = "us-east-1"


def get_token_encryption_key():
    client = boto3.client("secretsmanager", region_name=REGION)
    response = client.get_secret_value(SecretId=SECRET_ID)
    return json.loads(response["SecretString"])["TOKEN_ENCRYPTION_KEY"]
