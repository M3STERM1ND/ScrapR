# `OPEN-10` — Object storage

| | |
|---|---|
| **Decision ID** | `DEC-12` |
| **Closes** | `OPEN-10` |
| **Status** | Accepted |
| **Date** | 2026-09-12 |
| **Owner** | Both |
| **Blocks released** | Phase 4 uploads; Phase 7 export artifacts |
| **Affects** | `REQ-TECH-004`, `REQ-DOC-002`, `REQ-SEC-004`, `REQ-SEC-005`, `REQ-EXP-008`, implementation plan §10, §13 |

---

## 1. Decision

**The S3 API is the contract. Cloudflare R2 is the provider. MinIO is the
local one.**

Two halves, and separating them is the point. `N-05` observed that
"S3-compatible" names an API surface rather than a vendor and therefore cannot
answer `OPEN-10` — correct, and the answer is to name both: the surface the
code is written against, and the implementation behind it in each environment.

---

## 2. Why the surface is decided before the vendor

Implementation plan §13 requires local development to run on
`docker compose`, "Postgres **and storage**". That is not a convenience: an
upload path that only exists in production is one nobody tests, and
`REQ-DOC-010 AC-1` puts server-side limit enforcement in exactly that path.

So whatever the vendor, the code has to speak a protocol something can serve
locally. The S3 API is the only object-storage surface with a mature local
implementation, mature SDKs in the language this backend is written in, and
presigned-URL semantics that §10 already depends on.

Naming the surface first also makes the vendor genuinely swappable. Switching
from R2 to S3 to Backblaze is an endpoint and a credential; switching away from
a proprietary SDK is a rewrite of the upload and export paths.

---

## 3. Why R2 and not the others

Three candidates were compared against what this product actually does.

| | Storage /GB-mo | Egress /GB | S3 API | Local equivalent |
|---|---|---|---|---|
| **Cloudflare R2** | $0.015 | **$0.00** | Yes, SigV4 | MinIO |
| AWS S3 | $0.023 | $0.09 | Native | MinIO |
| Vercel Blob | $0.023 | $0.05 | **No** | None |

**Vercel Blob is rejected despite `DEC-03`.** Vercel is the deployment platform
and the native option would normally win on integration — except it has no S3
API, so there is no local equivalent and §13's docker-compose requirement
cannot be met without a second code path. It is also, by report, R2 underneath
with a markup, which makes paying for it to lose the S3 surface a poor trade.

**S3 is rejected on egress**, and egress is the axis that matters here. Uploads
are small and bounded (`DEC-13`), but Phase 7 generates PDF and PPTX exports
that users download — a read-heavy artifact workload is precisely where
$0.09/GB compounds and $0.00/GB does not.

**R2 satisfies the security requirements as written.** Encryption at rest with
provider-managed keys (`REQ-SEC-004 AC-1`), private buckets that are not
publicly listable (`REQ-SEC-005 AC-1`), and SigV4 presigned URLs for
time-limited authorized access (`REQ-SEC-005 AC-2`, `REQ-EXP-008`).

---

## 4. What this does not give us

Stated because it is the one place R2 is weaker than S3 and a future
requirement could turn on it: **R2 has no customer-managed key equivalent to
S3's KMS.** `REQ-SEC-004 AC-1` asks that uploads and artifacts be encrypted at
rest, which provider-managed keys satisfy. A later requirement for
customer-managed or customer-supplied keys would not be satisfiable on R2, and
would be a reason to revisit this — the S3 surface is what makes that a
migration rather than a rewrite.

---

## 5. Consequences

### 5.1 Code

A storage port with two implementations behind one interface: presign a PUT,
presign a GET, delete. `boto3` or `aiobotocore` against an endpoint URL, which
is the same client for R2 and MinIO — the endpoint and credentials differ and
nothing else does.

The API **never proxies file bytes** (§10). It signs and it records; the
browser talks to storage directly.

### 5.2 Local development

MinIO joins `docker compose` beside Postgres, so `docker compose up -d`
continues to be the whole local setup. A developer with no cloud account can
exercise the entire upload path.

### 5.3 Deletion

`REQ-SEC-008` requires a deleted file leave storage, and the schema already
anticipates the gap: `uploads.deleted_at` documents the row outliving the
object as the expected state between the two steps.

### 5.4 What stays open

`N-01` (Postgres hosting) and `N-02` (where the API runs) are untouched. R2 is
reachable over HTTPS from anywhere, so this decision constrains neither — which
is deliberate, because those two set the deployment shape and a storage choice
should not pre-empt them.

---

## 6. Known risk

**Zero-egress pricing is a commercial position, not a physical property.** The
whole cost argument in §3 rests on Cloudflare continuing not to charge for
egress. If that changes, the numbers move to roughly S3's and the reason for
choosing R2 over the incumbent largely evaporates.

That is survivable precisely because of §2: the code is written against the S3
API, so the response is a configuration change and a data migration rather than
a rewrite. The decision worth defending here is the surface. The vendor is a
row in a table.

---

## Sources

- [Cloudflare R2 vs AWS S3 in 2026](https://www.kunalganglani.com/blog/cloudflare-r2-vs-aws-s3)
- [Cloudflare R2 Pricing vs AWS S3, Azure, GCS (2026)](https://shattered.io/cloudflare-r2-vs-aws-s3-pricing-2026/)
- [Vercel Blob Storage: When It Makes Sense (and When It Doesn't)](https://www.edge-cases.com/nextjs/vercel-blob-storage-patterns)
- [Cloudflare R2 presigned URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/)
