"""One-time, local-only Secret Manager bootstrap for Hidden Tower Defence.

Run only from an authenticated operator environment. Values are read from the
current process environment and are never printed or written to Terraform.
"""

import argparse
import os
import secrets

from google.cloud import secretmanager

SECRET_SOURCES = {
    "secret--hiddentowerdefence--prod--apify-api-token": "APIFY_API_TOKEN",
    "secret--hiddentowerdefence--prod--hiddenlayer-client-id": "HiddenLayer_API_ClientID",
    "secret--hiddentowerdefence--prod--hiddenlayer-client-secret": "HiddenLayer_API_ClientSecret",
    "secret--hiddentowerdefence--prod--nvidia-api-key": "NVIDIA_nemotron-3-ultra-550b-a55b_API_KEY",
    "secret--hiddentowerdefence--prod--operator-token": "OPERATOR_TOKEN",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="smp-shared-prod")
    parser.add_argument("--create-missing", action="store_true")
    arguments = parser.parse_args()

    client = secretmanager.SecretManagerServiceClient()
    for secret_id, environment_name in SECRET_SOURCES.items():
        value = os.environ.get(environment_name)
        if environment_name == "OPERATOR_TOKEN" and not value:
            value = secrets.token_urlsafe(32)
        if not value:
            raise RuntimeError(f"{environment_name} is required and was not provided")
        parent = f"projects/{arguments.project}"
        name = f"{parent}/secrets/{secret_id}"
        try:
            client.get_secret(request={"name": name})
        except Exception as error:
            if not arguments.create_missing:
                raise RuntimeError(
                    f"{secret_id} does not exist; run Terraform first"
                ) from error
            client.create_secret(
                request={
                    "parent": parent,
                    "secret_id": secret_id,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
        client.add_secret_version(
            request={"parent": name, "payload": {"data": value.encode()}}
        )
        print(f"Stored a new version for {secret_id}")


if __name__ == "__main__":
    main()
