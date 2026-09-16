#!/usr/bin/env python3

import logging
import os
import sys
from pathlib import Path
from urllib.parse import quote

import requests
import yaml
from dotenv import load_dotenv
from pymongo import MongoClient


BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

RULES_FILE = BASE_DIR / "rules.yaml"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / "automation.log"


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DATABASE = os.getenv("MONGO_DATABASE", "HotelRates_Live")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "Input")

JENKINS_URL = os.getenv(
    "JENKINS_URL",
    "https://jenkins.aggregateintelligence.com"
).rstrip("/")

JENKINS_USERNAME = os.getenv("JENKINS_USERNAME")
JENKINS_API_TOKEN = os.getenv("JENKINS_API_TOKEN")

JENKINS_FOLDER = os.getenv(
    "JENKINS_FOLDER",
    "DC Support Scripts Handling"
)

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))


# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("constraint_automation")


# ---------------------------------------------------------
# Load rules
# ---------------------------------------------------------

def load_rules():
    with open(RULES_FILE, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    if not config or "rules" not in config:
        raise ValueError("rules.yaml is invalid")

    return config["rules"]


# ---------------------------------------------------------
# Trigger Jenkins
# ---------------------------------------------------------

def trigger_jenkins(job_name):
    folder = quote(JENKINS_FOLDER, safe="")
    job = quote(job_name, safe="")

    url = (
        f"{JENKINS_URL}"
        f"/job/{folder}"
        f"/job/{job}"
        f"/build"
    )

    response = requests.post(
        url,
        auth=(JENKINS_USERNAME, JENKINS_API_TOKEN),
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code not in (200, 201, 202):
        raise RuntimeError(
            f"Jenkins trigger failed: HTTP {response.status_code}"
        )


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main():

    try:
        rules = load_rules()

        client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=REQUEST_TIMEOUT * 1000,
            connectTimeoutMS=REQUEST_TIMEOUT * 1000,
        )

        client.admin.command("ping")

        collection = client[MONGO_DATABASE][MONGO_COLLECTION]

        total_modified = 0
        jobs_to_trigger = set()

        for rule in rules:

            username = rule["username"]
            websitecode = int(rule["websitecode"])
            old_status = int(rule["old_status"])
            new_status = int(rule["new_status"])
            jenkins_job = rule["jenkins_job"]

            result = collection.update_many(
                {
                    "username": username,
                    "websitecode": websitecode,
                    "status": old_status,
                },
                {
                    "$set": {
                        "status": new_status
                    }
                },
            )

            modified = result.modified_count

            total_modified += modified

            # Only log when something actually changed
            if modified > 0:
                logger.info(
                    "websitecode=%s modified=%s",
                    websitecode,
                    modified,
                )

                jobs_to_trigger.add(jenkins_job)

        client.close()

        # Trigger each Jenkins job only once
        for job in sorted(jobs_to_trigger):
            trigger_jenkins(job)

            logger.info(
                "Jenkins triggered: %s",
                job,
            )

        # Nothing happened
        if total_modified == 0:
            logger.info(
                "No changes found. Jenkins not triggered."
            )

        return 0

    except Exception as exc:

        logger.error(
            "Automation failed: %s",
            exc,
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())