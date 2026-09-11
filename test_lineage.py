from openlineage.client import OpenLineageClient
from openlineage.client.run import RunEvent, RunState, Run, Job, Dataset
from datetime import datetime
import uuid

# Correct configuration structure for OpenLineage 2.x+
client = OpenLineageClient(
    config={
        "transport": {
            "type": "http",
            "url": "http://localhost:5000"
        }
    }
)

# Generate execution run ID
run_id = str(uuid.uuid4())

# 1. Emit START event
client.emit(
    RunEvent(
        eventType=RunState.START,
        eventTime=datetime.utcnow().isoformat() + "Z",
        run=Run(runId=run_id),
        job=Job(namespace="pcf_pipeline", name="postgres_to_minio_sync"),
        inputs=[Dataset(namespace="postgres://pcf_postgres:5432", name="pcf_db.public.pcf_analytics_view")],
        outputs=[Dataset(namespace="s3://pcf_minio:9000", name="pcf-analytics-bucket/raw_data.parquet")],
        producer="pcf_script"
    )
)

# 2. Emit COMPLETE event
client.emit(
    RunEvent(
        eventType=RunState.COMPLETE,
        eventTime=datetime.utcnow().isoformat() + "Z",
        run=Run(runId=run_id),
        job=Job(namespace="pcf_pipeline", name="postgres_to_minio_sync"),
        inputs=[Dataset(namespace="postgres://pcf_postgres:5432", name="pcf_db.public.pcf_analytics_view")],
        outputs=[Dataset(namespace="s3://pcf_minio:9000", name="pcf-analytics-bucket/raw_data.parquet")],
        producer="pcf_script"
    )
)

print(f"Successfully emitted START & COMPLETE events for Run ID: {run_id}")