# NexaKind — Cloud Deployment Guide (CA1, Task 1)

Deploy the NexaKind Flask + MySQL app to **AWS EC2** using Docker Compose.
Everything below has been tested locally with `docker compose` before writing.

---

## 0. What gets deployed

| Container | Image | Role |
|-----------|-------|------|
| `web`     | built from `Dockerfile` | Flask app served by gunicorn on port 8000 |
| `db`      | `mysql:8.0` | MySQL database (auto-created + seeded on start) |

The app seeds itself on every start with known demo data:
- **Donor login:** `donor@example.com` / password `11`
- 10 seeded Pune orphanages with needs, surpluses and updates

> RabbitMQ `worker.py` is **not** deployed — the app writes pledges directly to
> MySQL, so the queue is unused. It stays in the repo as a documented future
> async enhancement (good talking point for the viva).

---

## 1. Run it locally first (sanity check)

```bash
cp .env.example .env        # then edit .env: set a real DB_PASSWORD + SECRET_KEY, keep DB_HOST=db, DB_USER=root
docker compose up --build
```

Open <http://localhost> → log in as `donor@example.com` / `11`.
Stop with `Ctrl+C`; wipe the database volume with `docker compose down -v`.

---

## 2. Deploy on AWS EC2 (free tier)

### 2.1 Launch the instance
1. AWS Console → **EC2** → **Launch instance**.
2. Name: `nexakind`. AMI: **Ubuntu Server 22.04 LTS**. Type: **t2.micro** (free tier — or t3.small if you want more RAM).
3. Create/download a key pair (`.pem`).
4. **Security group** — allow inbound:
   - SSH (22) from *My IP*
   - HTTP (80) from *Anywhere* (0.0.0.0/0)
5. Storage: 16 GB. Launch.

### 2.2 Connect and install Docker
```bash
ssh -i your-key.pem ubuntu@<EC2_PUBLIC_IP>

sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER
newgrp docker          # apply group without re-login
```

### 2.3 Get the code onto the instance
```bash
git clone <your-repo-url> nexakind    # or scp the folder up (see note below)
cd nexakind
```
> No git remote? From your laptop:
> `scp -i your-key.pem -r ./dcdsproject-my-new-feature ubuntu@<EC2_PUBLIC_IP>:~/nexakind`
> (make sure `venv/` is deleted first — see Cleanup section).

### 2.4 Configure secrets and run
```bash
cp .env.example .env
nano .env               # set DB_PASSWORD + a long random SECRET_KEY; keep DB_HOST=db, DB_USER=root
docker compose up --build -d
```

### 2.5 Verify
```bash
docker compose ps                 # both services "running", db "healthy"
docker compose logs -f web        # watch: waiting for MySQL -> seeding -> gunicorn on :8000
```
Open **http://<EC2_PUBLIC_IP>** in a browser. Log in as `donor@example.com` / `11`.

That URL is your live demo link. 🎉

### 2.6 Handy operations
```bash
docker compose logs -f web     # tail app logs
docker compose restart web     # restart app (reseeds demo data)
docker compose down            # stop (keeps data volume)
docker compose down -v         # stop + wipe database
```

---

## 3. Repo cleanup BEFORE pushing / copying (important)

The committed `venv/` (2500+ files) and the `.zip` must not go to the cloud.

```bash
git rm -r --cached venv dcdsproject-my-new-feature.zip
echo "dcdsproject-my-new-feature.zip" >> .gitignore
git add .
git commit -m "Add Docker deployment; drop venv and zip from tracking"
```

`.env` is already gitignored — never commit real secrets.

---

## 4. Demo script (2–3 minutes, for the 4–6 Aug viva)

1. **Show the architecture** (one slide): browser → EC2 → gunicorn/Flask → MySQL, all in Docker containers on one AWS instance.
2. **Open the live URL** (`http://<EC2_PUBLIC_IP>`) — proves it's on the cloud, not localhost.
3. **Log in** as the donor → browse needs → **make a pledge** → show it appears in the dashboard (this is your "output correctness / validation").
4. **SSH in and show `docker compose ps` + `docker compose logs web`** — proves the containerised deployment and DB seeding.
5. **One sentence on scaling**: gunicorn runs 3 workers; MySQL is a separate container; the queue/worker is the planned async next step.

---

## 5. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `web` keeps restarting, logs say "database not ready" forever | Check `.env` `DB_PASSWORD` matches; `docker compose down -v` then `up` |
| Browser can't reach the site | Security group must allow inbound **HTTP 80** from anywhere |
| Port 80 in use locally on Windows | Change the mapping in `docker-compose.yml` to `"8080:8000"` and use `:8080` |
| Changes not showing | `docker compose up --build -d` to rebuild the image |
