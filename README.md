# 🕊️ SmartMatch – Smart Orphanage Donation & Resource Management System

> “Bridging donors, orphanages, and needs transparently.”

## 📖 Overview
**SmartMatch** is a full-stack Flask web application built to simplify and optimize the donation process for orphanages.  
It connects **donors** and **NGOs**, enabling transparent resource management and **smart rerouting of surplus donations** between orphanages.

This project was developed as part of the **Database & Computing for Data Science (DCDS)** lab at **Symbiosis Institute of Technology, Pune**.

---

## 🎯 Objectives
- Create a secure and role-based system for Donors and Orphanages.
- Track donations, pledges, and resource status in real-time.
- Enable rerouting of surplus items between orphanages.
- Analyze donation patterns using SQL queries and insights dashboard.

---

## ⚙️ Features
- 🧑‍🤝‍🧑 Role-based login for Donors and Orphanages  
- 🏠 NGO dashboard for managing needs and surpluses  
- 💸 Donor dashboard for tracking pledges and donation history  
- 🔁 Smart rerouting between orphanages based on surplus/deficit  
- 📊 Insights dashboard for analytics using advanced SQL queries  
- 🔐 Secure authentication with Flask-Login and Flask-Bcrypt  

---

## 🎯 The matching engine: ML recommendations and need freshness

The matching engine is built on the app's existing tables. Donation history is `ItemNeeded` rows with a `donor_id`, and NGO location is `Orphanage.city`.

| Piece | Where | What it does |
|-------|-------|--------------|
| Synthetic data | `smartmatch/synthetic.py`, `flask smartmatch-seed` | Simulates 18 months in Pune: 30 NGOs across 30 areas, 150 donors, ~1,200 gifts. Donors prefer certain categories, nearby NGOs and NGOs they've given to before. About half the NGOs never re-confirm their listings. |
| Recommender | `smartmatch/features.py`, `smartmatch/recommender.py` | Scores each NGO on category match, TF-IDF content similarity, proximity (haversine), past gifts to that NGO, and urgency. Weights are learned with pairwise logistic regression (`flask smartmatch-train`). The score is then down-weighted for NGOs whose listings are stale. |
| Freshness tracker | `smartmatch/freshness.py`, `flask smartmatch-scan` | Flags Active listings not updated in 30+ days (`is_stale`, `stale_since`). NGOs clear the flag with **Still needed** on their dashboard. |
| Alerts | `smartmatch/alerts.py`, `flask smartmatch-scan --alert` | Sends one email (SMTP) and/or WhatsApp (Twilio) message per NGO, with a 7-day cooldown. Every attempt is recorded in `AlertLog`. **Dry run by default.** |
| Dashboard | `/smartmatch/dashboard` (admin) | Shows KPIs, model metrics, ranked recommendations with per-feature bars, freshness age buckets, stale listings and the alert log. |
| REST API | `smartmatch/api.py` | `GET/POST /api/recommend`, `GET /api/stale-listings`, `POST /api/stale-listings/scan`. Needs the `X-API-Key` header or an admin session. |

```bash
flask create-db && flask seed-db && flask smartmatch-seed && flask smartmatch-train && flask smartmatch-scan
curl -H "X-API-Key: $SMARTMATCH_API_KEY" "http://localhost/api/recommend?donor_id=1&item_category=Food&donor_area=Kothrud"
```

**Evaluation.** `smartmatch-train` holds out the most recent 20% of gifts. Features for each gift use only data from before it, so nothing leaks from the future. It reports hit-rate@5 and MRR for three rankers: the learned weights, hand-set default weights, and nearest NGO only. On the default synthetic data, learned and default weights score about the same (~79% hit-rate@5, MRR ~0.52). Both clearly beat nearest-NGO (~69%, 0.44). The synthetic donors were generated from exactly these signals, so learning mainly confirms the hand-set weights. On real donation data the learned weights are what would adapt.

**Tests:** `pip install -r requirements-dev.txt && pytest` (runs on in-memory SQLite, with no MySQL or network).

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-------------|
| **Frontend** | HTML, Tailwind CSS |
| **Backend** | Python (Flask Framework) |
| **Database** | MySQL (via SQLAlchemy ORM) |
| **Authentication** | Flask-Login, Flask-Bcrypt |


---

## 🧩 Database Design

- **ER Diagram** includes entities like `Donor`, `Orphanage`, `Donation`, `DonationItem`, `Need`, and `Routing`.
- SQL schema ensures data integrity through proper primary and foreign key relationships.

---

## 📸 Screenshots
(Add your app screenshots here — login page, dashboard, insights, rerouting interface.)

---

## 💡 Results
- Transparent donation tracking  
- Secure data management  
- Smart redistribution of resources  
- Functional analytics and rerouting module  

---

## 🧑‍💻 Contributors
- **Hardik Gulati**  
- **Chahat Patel**  
- **Dakshesh Chauhan**

Under the guidance of **Prof. Dr. Shruti Sunnad**

---

## 🧾 References
- Flask Web Development – *Miguel Grinberg*  
- Flask-SQLAlchemy Documentation  
- Flask-Login & Flask-Bcrypt Documentation  
- Tailwind CSS Documentation

---

## 📬 Contact
If you'd like to connect or discuss this project further:
- [LinkedIn - Hardik Gulati](your-linkedin-url)
- [Email - your-email@example.com]

---

⭐ **If you like this project, consider giving it a star!**
