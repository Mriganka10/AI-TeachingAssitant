# AWS Deployment Walkthrough

Last updated: 10 September 2026.

```text
professoraihub.com
  -> CloudFront (TLS, existing domain)
  -> shared ALB (private origin-header route)
  -> Professor AI ECS web service
       -> dedicated logical PostgreSQL database on shared RDS
       -> private S3 tenant prefixes
       -> SQS queue -> Professor AI ECS worker -> DLQ
```

CloudFront preserves the purchased domain and certificate. The origin header selects the correct application target group. ECS packs small isolated services onto shared ARM capacity, avoiding one always-on EC2 fleet per application. The web stays responsive because model and document work executes in the worker.

SQS provides buffering and retry. The database job row is the user-visible status source; the queue message contains only its identifier. The worker reloads authoritative state and deletes the message only on success. S3 carries large files rather than PostgreSQL.

For a release, validate the image, migrate compatibly, deploy worker then web, wait for healthy targets, and smoke-test login, upload, generation, polling, and downloads. Monitor target health, HTTP errors, task restarts, queue age/DLQ, database, and model failures. Roll back task definitions if checks fail. The paused Elastic Beanstalk stack is historical rollback insurance, not normal traffic.
