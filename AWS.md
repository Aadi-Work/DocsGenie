# AWS stack (Bedrock + S3) — EC2 ready

## What changed
- **LLM:** Amazon Bedrock Claude **Sonnet 4.5** (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`)
- **Template in:** `s3://info-nexus-s3/Template/` ([console](https://us-east-1.console.aws.amazon.com/s3/upload/info-nexus-s3?region=us-east-1&prefix=Template/))
- **Documents out:** `s3://info-nexus-s3/Documents/` ([console](https://us-east-1.console.aws.amazon.com/s3/buckets/info-nexus-s3?region=us-east-1&prefix=Documents/&showversions=false))
- OneDrive Graph routes are no longer mounted; use `/api/s3/*` instead

## Credentials (pick one)
1. **EC2 instance profile (recommended):** attach an IAM role with Bedrock + S3 permissions; leave `AWS_ACCESS_KEY_ID` empty.
2. **Env keys (local / temporary):** set in `backend/.env`:
   ```
   AWS_ACCESS_KEY_ID=...
   AWS_SECRET_ACCESS_KEY=...
   AWS_SESSION_TOKEN=...   # if using STS
   AWS_REGION=us-east-1
   ```

### IAM (minimum)
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": ["arn:aws:s3:::info-nexus-s3"],
      "Condition": {
        "StringLike": { "s3:prefix": ["Template/*", "Documents/*"] }
      }
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucketVersions", "s3:GetObjectVersion"],
      "Resource": [
        "arn:aws:s3:::info-nexus-s3/Template/*",
        "arn:aws:s3:::info-nexus-s3/Documents/*"
      ]
    }
  ]
}
```
Also enable model access for Claude Sonnet 4.5 in the Bedrock console (us-east-1).

## APIs
| Path | Purpose |
|------|---------|
| `GET /api/health` | App + Bedrock config + S3 head_bucket |
| `GET /api/s3/templates` | List `Template/` |
| `GET /api/s3/documents` | List `Documents/` |
| `POST /api/s3/search` | Ranked keyword search |
| `POST /api/chat` | Chatbot: hybrid RAG + S3 + Bedrock |
| `POST /api/compose` | Generate; uploads result to `Documents/` |

## Chat / search algorithm
1. Intent classify via Bedrock (search / create / s3_search / …)
2. Catalog: Chroma hybrid retrieval over published templates
3. S3: tokenized key/name scoring (exact > name tokens > path)
4. Create flow: clarify → Bedrock fill placeholders → generate → put `Documents/YYYY/MM/DD/...`

## Run locally
```bat
cd backend
copy .env.example .env
REM fill AWS keys or use `aws configure`
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Run on EC2
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Prefer IAM role — do not put long-lived keys on the box
export LLM_PROVIDER=bedrock
export AWS_REGION=us-east-1
export S3_BUCKET=info-nexus-s3
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
```
Open security group: TCP 8000 (or put nginx/ALB in front). Set `CORS_ORIGINS` to your frontend origin.

## Share credentials
Paste `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (and session token if any) into `backend/.env` — do not commit them.
