# billing.py — Stripe integration for Kabroda
import os
import stripe
from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import JSONResponse

router = APIRouter()

# Set Stripe secret key from environment
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Optional: Environment-based price IDs
PRICE_IDS = {
    "monthly": os.getenv("STRIPE_PRICE_MONTHLY"),
    "semi": os.getenv("STRIPE_PRICE_SEMI"),
    "annual": os.getenv("STRIPE_PRICE_ANNUAL"),
}

@router.post("/create-checkout-session")
async def create_checkout_session(request: Request):
    body = await request.json()
    plan = body.get("plan")

    if plan not in PRICE_IDS:
        raise HTTPException(status_code=400, detail="Invalid plan selected.")

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": PRICE_IDS[plan], "quantity": 1}],
            success_url=f"{os.getenv('PUBLIC_BASE_URL')}/account?success=true",
            cancel_url=f"{os.getenv('PUBLIC_BASE_URL')}/pricing?cancelled=true",
        )
        return {"sessionId": checkout_session["id"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Handle subscription events
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        # TODO: Update user subscription status in DB

    return JSONResponse(content={"status": "success"})
