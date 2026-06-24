#!/bin/bash
# Run this once to wire Cloud Logging → Pub/Sub for LogLM.
# Prerequisites: gcloud auth login + gcloud config set project YOUR_PROJECT_ID

set -e  # stop immediately if any command fails

# Grab your active project ID from gcloud config so you don't have to type it
PROJECT_ID=$(gcloud config get-value project)

# Names for the resources we're creating — change these if you want
TOPIC="loglm-logs"           # the Pub/Sub channel logs get dropped into
SUBSCRIPTION="loglm-logs-sub" # LogLM's handle to pull messages from that channel
SINK="loglm-sink"             # the Cloud Logging filter that routes logs to the topic
SERVICE_NAME="loglm-test-app" # only forward logs from this Cloud Run service

echo "Setting up LogLM pipeline for project: $PROJECT_ID"

# 1. Create the Pub/Sub topic
#    This is the "pipe" between Cloud Logging and LogLM
gcloud pubsub topics create $TOPIC

# 2. Create a subscription on that topic
#    LogLM will pull messages through this subscription
#    --ack-deadline=60 means LogLM has 60 seconds to process a message before
#    Pub/Sub considers it undelivered and retries it
gcloud pubsub subscriptions create $SUBSCRIPTION \
  --topic=$TOPIC \
  --ack-deadline=60

# 3. Create the log sink
#    This tells Cloud Logging: "for logs matching this filter, forward to Pub/Sub"
#    The filter says: only logs from our Cloud Run service, severity WARNING or above
gcloud logging sinks create $SINK \
  pubsub.googleapis.com/projects/$PROJECT_ID/topics/$TOPIC \
  --log-filter='resource.type="cloud_run_revision" AND resource.labels.service_name="'$SERVICE_NAME'" AND severity>="WARNING"'

# 4. The log sink runs as a GCP-managed service account — it needs permission to
#    publish messages to our topic or it'll be silently blocked
#    This command grants it that permission
SINK_SA=$(gcloud logging sinks describe $SINK --format='value(writerIdentity)')

gcloud pubsub topics add-iam-policy-binding $TOPIC \
  --member="$SINK_SA" \
  --role="roles/pubsub.publisher"

echo ""
echo "Done. Pipeline is live:"
echo "  Cloud Logging → $SINK → projects/$PROJECT_ID/topics/$TOPIC → $SUBSCRIPTION"
echo ""
echo "Note these down — you'll need them when we configure LogLM:"
echo "  PUBSUB_PROJECT_ID=$PROJECT_ID"
echo "  PUBSUB_SUBSCRIPTION=$SUBSCRIPTION"
