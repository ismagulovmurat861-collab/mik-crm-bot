from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from openai import OpenAI

from sqlalchemy import Column, Integer, String
from database import Base, engine, SessionLocal

import os

# =====================================================
# APP
# =====================================================

app = FastAPI(title="MiK AI CRM")

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

# =====================================================
# DATABASE MODEL
# =====================================================

class Lead(Base):

    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(String)
    status = Column(String)

# create tables
Base.metadata.create_all(bind=engine)

# =====================================================
# AI SALES
# =====================================================

def ai_sales(text):

    prompt = f"""
Ты AI агент MiK Real Estate.

Цель:
- назначить встречу
- выявить интерес
- продать объект

Клиент:
{text}
"""

    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "user", "content": prompt}
        ]
    )

    return res.choices[0].message.content

# =====================================================
# LEAD API
# =====================================================

@app.post("/lead")
async def lead(request: Request):

    data = await request.json()

    text = data.get("text", "")

    # AI RESPONSE
    reply = ai_sales(text)

    # STATUS
    status = "new"

    if "встреч" in reply.lower():
        status = "meeting"

    # SAVE TO DB
    db = SessionLocal()

    new_lead = Lead(
        text=text,
        status=status
    )

    db.add(new_lead)
    db.commit()

    return {
        "status": status,
        "reply": reply
    }

# =====================================================
# CRM API
# =====================================================

@app.get("/crm")
def crm():

    db = SessionLocal()

    leads = db.query(Lead).all()

    result = []

    for l in leads:

        result.append({
            "id": l.id,
            "text": l.text,
            "status": l.status
        })

    return result

# =====================================================
# BI API
# =====================================================

@app.get("/bi")
def bi():

    db = SessionLocal()

    leads = db.query(Lead).count()

    meetings = db.query(Lead).filter(
        Lead.status == "meeting"
    ).count()

    conversion = 0

    if leads > 0:
        conversion = round(
            meetings / leads * 100,
            2
        )

    return {
        "leads": leads,
        "meetings": meetings,
        "conversion": conversion
    }

# =====================================================
# DASHBOARD
# =====================================================

@app.get("/", response_class=HTMLResponse)
def dashboard():

    db = SessionLocal()

    leads = db.query(Lead).count()

    meetings = db.query(Lead).filter(
        Lead.status == "meeting"
    ).count()

    html = f"""
    <html>

    <head>
        <title>MiK CRM</title>

        <style>

        body {{
            background:#111;
            color:white;
            font-family:Arial;
            padding:30px;
        }}

        .card {{
            background:#1d1d1d;
            padding:20px;
            border-radius:15px;
            margin-bottom:20px;
        }}

        </style>

    </head>

    <body>

        <h1>🏢 MiK AI CRM</h1>

        <div class="card">
            <h2>📩 Leads</h2>
            <p>{leads}</p>
        </div>

        <div class="card">
            <h2>📅 Meetings</h2>
            <p>{meetings}</p>
        </div>

        <div class="card">
            <h2>📊 Conversion</h2>
            <p>
            {round(meetings / leads * 100, 2) if leads else 0}%
            </p>
        </div>

    </body>
    </html>
    """

    return html
