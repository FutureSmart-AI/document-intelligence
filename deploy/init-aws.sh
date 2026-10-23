#!/bin/sh
set -eu
awslocal s3api head-bucket --bucket di-documents 2>/dev/null || awslocal s3api create-bucket --bucket di-documents
awslocal sqs create-queue --queue-name di-ingestion.fifo --attributes FifoQueue=true,ContentBasedDeduplication=true,VisibilityTimeout=3600
