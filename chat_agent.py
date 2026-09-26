import os
import json
import re
import threading
from decimal import Decimal
from datetime import datetime
from groq import Groq


def normalize_purchase_amount(value):
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:
        return None
    return amount if amount > 0 else None


def normalize_plan_key(value):
    if value is None:
        return ""
    text = str(value).strip().upper()
    return re.sub(r"[^A-Z0-9]+", "_", text).strip("_")


def get_groq_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    return Groq(api_key=api_key)


def clean_whatsapp_text(text):
    """Normalize model markdown to WhatsApp-friendly text."""
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"(?m)^\s*\*{2,}\s*$", "", cleaned)
    cleaned = re.sub(r"\*{2,}", "", cleaned)
    cleaned = re.sub(r"^\s*\*+", "", cleaned).strip()
    cleaned = re.sub(r"\*+\s*$", "", cleaned).strip()
    return cleaned


def requested_photo_receipt(text):
    normalized = str(text or "").lower()
    return "receipt" in normalized and any(
        word in normalized for word in ("photo", "image", "picture", "png", "send", "download")
    )


def build_context(history, max_user_turns=3):
    """Keep full history in the database while sending complete recent turns to Groq."""
    if not isinstance(history, list):
        history = []
    valid = [
        message for message in history
        if isinstance(message, dict) and message.get("role") in {"system", "user", "assistant", "tool"}
    ]
    system = next((message for message in valid if message.get("role") == "system"), {"role": "system", "content": SYSTEM_PROMPT})
    conversation = [message for message in valid if message.get("role") != "system"]
    user_indexes = [index for index, message in enumerate(conversation) if message.get("role") == "user"]
    if len(user_indexes) <= max_user_turns:
        return [system] + conversation

    start = user_indexes[-max_user_turns]
    older = conversation[:start]
    recent = conversation[start:]
    memory_lines = []
    for message in older:
        if message.get("role") in {"user", "assistant"} and message.get("content"):
            content = clean_whatsapp_text(message["content"])
            if content:
                memory_lines.append(f"{message['role']}: {content[:300]}")
    memory = "\n".join(memory_lines[-20:])
    context = [system]
    if memory:
        context.append({"role": "system", "content": f"Long-term conversation memory from earlier messages:\n{memory}"})
    context.extend(recent)
    return context

SYSTEM_PROMPT = """You are WAJ VTU Assistant, a helpful AI that allows users in Nigeria to buy Data, Airtime, Cable TV, Electricity, Betting Top-ups, and Education PINs.
You have access to tools to fetch plans and execute transactions. 

RULES:
1. When a user asks for a service, FIRST use the fetching tool to see available plans and their EXACT `plan_code` and `amount`.
2. FORMAT BEAUTIFULLY FOR WHATSAPP: Use relevant emojis (e.g. 🌐, 📺, ⚡, 💸) and WhatsApp bold text (e.g. *1GB* - *₦500*) to make the chat visually stunning and highly engaging. Present options as a clean, numbered list with double line breaks between items. DO NOT summarize the list or leave any plans out. You MUST list every single plan. DO NOT show the `plan_code` or internal codes to the user, only show the plan name and price.
3. Before any purchase, repeat the selected plan, exact price, recipient, and ask the user to confirm. Only purchase after a clear confirmation. When confirmed, use the exact `plan_code` and `amount`.
4. If a purchase fails, inform the user politely.
5. KEEP YOUR RESPONSES SHORT AND FRIENDLY.
6. **MULTI-LANGUAGE SUPPORT**: If the user speaks to you in Hausa, Igbo, Yoruba, or Nigerian Pidgin, YOU MUST RESPOND IN THAT EXACT NATIVE LANGUAGE. Translate your responses naturally while executing the underlying tools normally in English.
7. If the user asks for their transaction history or receipts, use `get_transaction_history`.
8. If the user asks for a recurring/scheduled transaction (e.g. "buy this every Friday"), use `schedule_task`.
9. If the user is extremely angry, stuck, or explicitly asks to speak to a human/customer care, immediately use `escalate_to_human`.
"""

def define_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "get_data_plans",
                "description": "Fetch available data plans for a specific network.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "network": {"type": "string", "enum": ["MTN", "AIRTEL", "GLO", "9MOBILE"]}
                    },
                    "required": ["network"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "buy_data",
                "description": "Execute a data purchase. MUST provide the exact plan_code and amount retrieved from get_data_plans.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "network": {"type": "string", "enum": ["MTN", "AIRTEL", "GLO", "9MOBILE"]},
                        "plan_code": {"type": "string", "description": "The exact variation_code or plan_code"},
                        "amount": {"type": "number", "description": "The exact cost"},
                        "phone": {"type": "string", "description": "The 11-digit recipient phone number"},
                        "confirm": {"type": "boolean", "description": "Set to true only after the user explicitly confirms the exact plan, price, and recipient."}
                    },
                    "required": ["network", "plan_code", "amount", "phone"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "buy_airtime",
                "description": "Execute an airtime recharge.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "network": {"type": "string", "enum": ["MTN", "AIRTEL", "GLO", "9MOBILE"]},
                        "amount": {"type": "number", "description": "The amount to recharge in Naira"},
                        "phone": {"type": "string", "description": "The 11-digit recipient phone number"}
                    },
                    "required": ["network", "amount", "phone"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_wallet_balance",
                "description": "Check the user's current wallet balance.",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_cable_plans",
                "description": "Fetch available cable TV plans (DSTV, GOTV, STARTIMES).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string", "enum": ["DSTV", "GOTV", "STARTIMES"]}
                    },
                    "required": ["provider"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "buy_cable",
                "description": "Execute a cable TV subscription.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string", "enum": ["DSTV", "GOTV", "STARTIMES"]},
                        "smartcard": {"type": "string", "description": "The smartcard/IUC number"},
                        "plan_code": {"type": "string", "description": "The exact plan_code from get_cable_plans"},
                        "amount": {"type": "number", "description": "The exact cost of the plan"}
                    },
                    "required": ["provider", "smartcard", "plan_code", "amount"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "verify_meter",
                "description": "Verify an electricity meter number before payment.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "disco": {"type": "string", "enum": ["IKEDC", "EKEDC", "AEDC", "IBEDC"]},
                        "meter_number": {"type": "string"},
                        "meter_type": {"type": "string", "enum": ["PREPAID", "POSTPAID"]}
                    },
                    "required": ["disco", "meter_number", "meter_type"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "pay_electricity",
                "description": "Execute an electricity bill payment. Requires meter verification first.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "disco": {"type": "string", "enum": ["IKEDC", "EKEDC", "AEDC", "IBEDC"]},
                        "meter_number": {"type": "string"},
                        "meter_type": {"type": "string", "enum": ["PREPAID", "POSTPAID"]},
                        "amount": {"type": "number"}
                    },
                    "required": ["disco", "meter_number", "meter_type", "amount"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_funding_account",
                "description": "Create a one-time Paystack checkout link for adding a specific amount to the user's wallet. Requires the user's email and the amount to add.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string", "description": "User's email address"},
                        "amount": {"type": "number", "description": "The amount to add to the wallet in Naira"}
                    },
                    "required": ["amount"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_transaction_history",
                "description": "Fetch the user's most recent transactions to provide history or receipts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Number of recent transactions to fetch (e.g. 5)"}
                    },
                    "required": ["limit"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "escalate_to_human",
                "description": "Escalate the chat to a human admin and disable AI responses for this user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "The reason for escalation"}
                    },
                    "required": ["reason"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "schedule_task",
                "description": "Schedule a recurring VTU purchase. The tool_name must be a purchase tool (e.g., buy_data, buy_airtime). tool_kwargs must be the exact JSON dictionary of arguments for that tool.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "frequency": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
                        "tool_name": {"type": "string", "description": "The name of the tool to run, e.g. buy_data"},
                        "tool_kwargs": {"type": "string", "description": "A JSON-encoded string of the arguments for the tool"}
                    },
                    "required": ["frequency", "tool_name", "tool_kwargs"]
                }
            }
        }
    ]

def execute_tool(app, db, user, provider_phone, name, kwargs):
    from provider import (
        fetch_data_variations, process_data_purchase, process_airtime_purchase,
        fetch_cable_plans, verify_smartcard, process_cable_tv,
        verify_meter as provider_verify_meter, process_electricity_payment,
        fetch_education_packages
    )
    from app import get_markup, settle_transaction
    from models import Transaction, ScheduledTask
    

    if name == "get_wallet_balance":
        return {"status": "success", "balance": float(user.wallet_balance)}
        
    elif name == "get_transaction_history":
        limit = kwargs.get("limit", 5)
        txs = Transaction.query.filter_by(user_id=user.id).order_by(Transaction.created_at.desc()).limit(limit).all()
        history = [{"reference": t.reference, "type": t.type, "amount": float(t.amount), "status": t.status, "date": str(t.created_at)} for t in txs]
        return {"status": "success", "transactions": history}
        
    elif name == "escalate_to_human":
        user.is_escalated = True
        db.session.commit()
        admin_phone = os.getenv("ADMIN_PHONE")
        if admin_phone:
            from app import send_whatsapp_message
            send_whatsapp_message(admin_phone, f"⚠️ *Escalation Alert*\\nUser {user.phone} requested human support.\\nReason: {kwargs.get('reason')}")
        return {"status": "success", "message": "The chat has been escalated. You should tell the user an agent will reply shortly."}
        
    elif name == "schedule_task":
        frequency = kwargs.get("frequency")
        tool_name = kwargs.get("tool_name")
        try:
            tool_kwargs = json.loads(kwargs.get("tool_kwargs")) if isinstance(kwargs.get("tool_kwargs"), str) else kwargs.get("tool_kwargs")
        except:
            tool_kwargs = kwargs.get("tool_kwargs")
            
        from datetime import timedelta
        if frequency == "daily":
            next_run = datetime.utcnow() + timedelta(days=1)
        elif frequency == "weekly":
            next_run = datetime.utcnow() + timedelta(days=7)
        else:
            next_run = datetime.utcnow() + timedelta(days=30)
            
        task = ScheduledTask(
            user_id=user.id,
            frequency=frequency,
            next_run=next_run,
            tool_name=tool_name,
            tool_kwargs=tool_kwargs
        )
        db.session.add(task)
        db.session.commit()
        return {"status": "success", "message": f"Task scheduled to run {frequency} starting {next_run.strftime('%Y-%m-%d')}"}

        
    elif name == "get_data_plans":
        network = kwargs.get("network")
        variations = fetch_data_variations(network)
        if not variations:
            return {"status": "error", "message": "No plans available right now."}
        
        plans = []
        display_plans = []
        for index, plan in enumerate(variations, start=1):
            base_cost = Decimal(str(plan.get("variation_amount")))
            cost = (base_cost + get_markup(f"DATA_{network}", base_cost)).quantize(Decimal("0.01"))
            plans.append(
                f"- {plan.get('name')}: ₦{cost:,.2f} (System Code: {plan.get('variation_code')})"
            )
            display_plans.append(f"{index}. {plan.get('name')}: ₦{cost:,.2f}")
        return {
            "status": "success",
            "plans_list": "\n".join(plans),
            "display_plans": "\n\n".join(display_plans),
            "message": "Present these options to the user clearly. Do not show the System Code to the user.",
        }
        
    elif name == "buy_data":
        network = str(kwargs.get("network") or "").upper()
        plan_code = kwargs.get("plan_code")
        requested_amount = kwargs.get("amount")
        phone = kwargs.get("phone")

        requested_key = normalize_plan_key(plan_code)
        available_plans = fetch_data_variations(network)
        selected_plan = next(
            (
                plan for plan in available_plans
                if normalize_plan_key(plan.get("variation_code")) == requested_key
                or normalize_plan_key(plan.get("name")) == requested_key
            ),
            None,
        )
        if not selected_plan:
            alias_variants = []
            for plan in available_plans:
                alias_variants.extend([
                    str(plan.get("variation_code") or "").strip(),
                    str(plan.get("name") or "").strip(),
                ])
            if requested_key:
                return {
                    "status": "error",
                    "message": f"Select a valid data plan. Available options include: {', '.join(alias_variants[:5])}",
                }
            return {"status": "error", "message": "Select a valid data plan."}

        plan_code = str(selected_plan.get("variation_code") or plan_code)
        base_amount = Decimal(str(selected_plan.get("variation_amount", "0")))
        charge_amount = (base_amount + get_markup(f"DATA_{network}", base_amount)).quantize(Decimal("0.01"))
        try:
            requested_amount = Decimal(str(requested_amount)).quantize(Decimal("0.01"))
        except Exception:
            return {"status": "error", "message": "The selected data price is invalid. Please fetch the plans again."}

        if requested_amount != charge_amount:
            return {
                "status": "error",
                "message": f"The price for this plan has changed. Current price is NGN {charge_amount:,.2f}. Please fetch the plans again before purchasing.",
            }

        confirmed = kwargs.get("confirm") is True or str(kwargs.get("confirm") or "").strip().lower() in {"true", "yes", "confirm", "confirmed"}
        if not confirmed:
            return {
                "status": "pending_confirmation",
                "message": f"Please confirm: you want to buy {selected_plan.get('name')} for NGN {charge_amount:,.2f} on {network} for {phone}. Reply YES to proceed.",
            }
        
        if user.wallet_balance < charge_amount:
            return {"status": "error", "message": f"Insufficient balance. Requires NGN {charge_amount:,.2f}. Wallet balance is NGN {user.wallet_balance:,.2f}"}
            
        user.wallet_balance -= charge_amount
        db.session.commit()
        
        result = process_data_purchase(phone, network, plan_code, float(base_amount))
        if result.get("status") == "SUCCESS":
            tx = Transaction(
                user_id=user.id,
                reference=result['reference'],
                amount=charge_amount,
                type='DATA',
                recipient=phone,
                provider_name=str(result.get("provider") or "unknown").lower()[:30],
                provider_reference=(result.get("provider_reference") or result.get("reference") or "")[:100],
                status='SUCCESS',
                description=f"{network} {plan_code} to {phone}"
            )
            db.session.add(tx)
            db.session.commit()
            return {"status": "success", "reference": result['reference'], "message": f"Successfully purchased data for {phone}"}
        else:
            user.wallet_balance += charge_amount
            db.session.commit()
            return {"status": "error", "message": result.get("reason", "Provider failed")}

    elif name == "buy_airtime":
        network = kwargs.get("network")
        amount = normalize_purchase_amount(kwargs.get("amount"))
        phone = kwargs.get("phone")
        if amount is None:
            return {"status": "error", "message": "Enter a valid positive airtime amount."}
        
        charge_amount = amount + get_markup("AIRTIME", amount)
        confirmed = kwargs.get("confirm") is True or str(kwargs.get("confirm") or "").strip().lower() in {"true", "yes", "confirm", "confirmed"}
        if not confirmed:
            return {
                "status": "pending_confirmation",
                "message": f"Please confirm: you want to buy airtime worth NGN {charge_amount:,.2f} for {phone} on {network}. Reply YES to proceed.",
            }

        if user.wallet_balance < charge_amount:
            return {"status": "error", "message": f"Insufficient balance. Requires NGN {charge_amount:,.2f}. Wallet balance is NGN {user.wallet_balance:,.2f}"}
            
        user.wallet_balance -= charge_amount
        db.session.commit()
        
        result = process_airtime_purchase(phone, network, float(amount))
        if result.get("status") == "SUCCESS":
            tx = Transaction(
                user_id=user.id,
                reference=result['reference'],
                amount=charge_amount,
                type='AIRTIME',
                recipient=phone,
                provider_name=str(result.get("provider") or "unknown").lower()[:30],
                provider_reference=(result.get("provider_reference") or result.get("reference") or "")[:100],
                status='SUCCESS',
                description=f"{network} Airtime to {phone}"
            )
            db.session.add(tx)
            db.session.commit()
            return {"status": "success", "reference": result['reference'], "message": f"Successfully sent NGN {amount:,.2f} airtime to {phone}"}
        else:
            user.wallet_balance += charge_amount
            db.session.commit()
            return {"status": "error", "message": result.get("reason", "Provider failed")}
            
    elif name == "get_cable_plans":
        provider = kwargs.get("provider")
        plans_raw = fetch_cable_plans(provider)
        if not plans_raw:
            return {"status": "error", "message": "No plans available right now."}
            
        plans = []
        for plan in plans_raw:
            base_cost = Decimal(str(plan["amount"]))
            cost = base_cost + get_markup("CABLE", base_cost)
            plans.append(
                f"- {plan.get('name')}: ₦{cost:,.2f} (System Code: {plan.get('code')})"
            )
        return {"status": "success", "plans_list": "\n".join(plans), "message": "Present these options to the user clearly. Do not show the System Code to the user."}
        
    elif name == "buy_cable":
        provider = kwargs.get("provider")
        smartcard = kwargs.get("smartcard")
        plan_code = kwargs.get("plan_code")
        amount = normalize_purchase_amount(kwargs.get("amount"))
        if amount is None:
            return {"status": "error", "message": "Select a valid cable plan price."}
        api_cost_value = kwargs.get("api_cost")
        api_discount_value = kwargs.get("api_discount_amount")
        if api_cost_value is None:
            matched_plan = next(
                (plan for plan in fetch_cable_plans(provider) if str(plan.get("code")) == str(plan_code)),
                None,
            )
            api_cost_value = matched_plan.get("amount", amount) if matched_plan else amount
            api_discount_value = matched_plan.get("discount_amount", "0") if matched_plan else "0"
        api_cost = Decimal(str(api_cost_value))
        api_discount = Decimal(str(api_discount_value or "0"))
        expected_amount = (api_cost + get_markup("CABLE", api_cost)).quantize(Decimal("0.01"))
        if amount != expected_amount:
            return {"status": "error", "message": f"The cable plan price has changed. Current price is NGN {expected_amount:,.2f}. Please fetch the plans again."}
        
        confirmed = kwargs.get("confirm") is True or str(kwargs.get("confirm") or "").strip().lower() in {"true", "yes", "confirm", "confirmed"}
        if not confirmed:
            return {
                "status": "pending_confirmation",
                "message": f"Please confirm: you want to renew {provider} with plan {plan_code} for NGN {amount:,.2f} on smartcard {smartcard}. Reply YES to proceed.",
            }

        if user.wallet_balance < amount:
            return {"status": "error", "message": f"Insufficient balance. Wallet balance is NGN {user.wallet_balance:,.2f}"}
            
        verification = verify_smartcard(provider, smartcard)
        if not verification.get("valid"):
            return {"status": "error", "message": verification.get("message", "Invalid smartcard")}
            
        user.wallet_balance -= amount
        db.session.commit()
        
        result = process_cable_tv(provider, smartcard, plan_code, float(api_cost), provider_phone)
        success = settle_transaction(
            user,
            result,
            amount,
            "CABLE",
            smartcard,
            f"{provider} {plan_code}",
            meta_data={
                "api_cost": str(api_cost),
                "api_discount_amount": str(api_discount),
                "markup_amount": str(amount - api_cost),
            },
        )
        if success and result.get("status") == "SUCCESS":
            tx = Transaction.query.filter_by(reference=result["reference"]).first()
            if tx is not None:
                tx.provider_name = str(result.get("provider") or tx.provider_name or "unknown").lower()[:30]
                tx.provider_reference = (result.get("provider_reference") or tx.provider_reference or result.get("reference") or "")[:100]
                db.session.commit()
        if success:
            return {"status": "success", "reference": result['reference'], "message": "Cable TV subscription successful"}
        else:
            return {"status": "error", "message": result.get("reason", "Provider failed")}

    elif name == "verify_meter":
        disco = kwargs.get("disco")
        meter = kwargs.get("meter_number")
        mtype = kwargs.get("meter_type")
        verification = provider_verify_meter(disco, meter, mtype)
        if verification.get("valid"):
            return {"status": "success", "message": "Meter verified successfully", "details": verification}
        return {"status": "error", "message": verification.get("message", "Invalid meter")}
        
    elif name == "pay_electricity":
        disco = kwargs.get("disco")
        meter = kwargs.get("meter_number")
        mtype = kwargs.get("meter_type")
        amount = normalize_purchase_amount(kwargs.get("amount"))
        if amount is None:
            return {"status": "error", "message": "Enter a valid positive electricity amount."}
        
        charge_amount = amount + get_markup("ELECTRICITY", amount)
        confirmed = kwargs.get("confirm") is True or str(kwargs.get("confirm") or "").strip().lower() in {"true", "yes", "confirm", "confirmed"}
        if not confirmed:
            return {
                "status": "pending_confirmation",
                "message": f"Please confirm: you want to pay NGN {charge_amount:,.2f} for meter {meter} under {disco}. Reply YES to proceed.",
            }

        if user.wallet_balance < charge_amount:
            return {"status": "error", "message": f"Insufficient balance. Required: NGN {charge_amount:,.2f}"}
            
        user.wallet_balance -= charge_amount
        db.session.commit()
        
        result = process_electricity_payment(disco, meter, mtype, float(amount), provider_phone)
        success = settle_transaction(user, result, charge_amount, "ELECTRICITY", meter, f"{disco} electricity payment")
        if success and result.get("status") == "SUCCESS":
            tx = Transaction.query.filter_by(reference=result["reference"]).first()
            if tx is not None:
                tx.provider_name = str(result.get("provider") or tx.provider_name or "unknown").lower()[:30]
                tx.provider_reference = (result.get("provider_reference") or tx.provider_reference or result.get("reference") or "")[:100]
                db.session.commit()
            return {"status": "success", "reference": result['reference'], "token": result.get("token"), "message": "Payment successful"}
        else:
            return {"status": "error", "message": result.get("reason", "Payment failed")}

    elif name == "get_funding_account":
        email = kwargs.get("email")
        if email and not user.email:
            user.email = email
            db.session.commit()

        if not user.email:
            return {"status": "error", "message": "I need an email address to set up your funding account. What's your email?"}

        try:
            amount = Decimal(str(kwargs.get("amount", "0"))).quantize(Decimal("0.01"))
        except Exception:
            amount = Decimal("0")
        if amount <= 0:
            return {"status": "error", "message": "Please provide a valid amount to add to your wallet."}

        from wallet_service import generate_payment_link
        result = generate_payment_link(user.email, amount, user.phone, pass_fee_to_user=True)
        if result.get("status") == "SUCCESS":
            return {
                "status": "success",
                "payment_url": result["payment_url"],
                "message": f"Open this secure Paystack link to add NGN {amount:,.2f} to your wallet: {result['payment_url']}",
            }
        return {"status": "error", "message": result.get("reason", "Could not create a Paystack payment link right now.")}

    elif name == "verify_betting":
        platform = kwargs.get("platform")
        account_id = kwargs.get("account_id")
        from provider import verify_betting_account
        verification = verify_betting_account(platform, account_id)
        if verification.get("valid"):
            return {"status": "success", "message": "Betting account verified", "account_name": verification.get("account_name")}
        return {"status": "error", "message": verification.get("message", "Invalid betting account")}

    elif name == "buy_betting":
        platform = kwargs.get("platform")
        account_id = kwargs.get("account_id")
        amount = normalize_purchase_amount(kwargs.get("amount"))
        if amount is None:
            return {"status": "error", "message": "Enter a valid positive betting amount."}
        from provider import process_betting_topup

        charge_amount = amount + get_markup("BETTING", amount)
        confirmed = kwargs.get("confirm") is True or str(kwargs.get("confirm") or "").strip().lower() in {"true", "yes", "confirm", "confirmed"}
        if not confirmed:
            return {
                "status": "pending_confirmation",
                "message": f"Please confirm: you want to fund betting account {account_id} with NGN {charge_amount:,.2f} on {platform}. Reply YES to proceed.",
            }

        if user.wallet_balance < charge_amount:
            return {"status": "error", "message": f"Insufficient balance. Required: NGN {charge_amount:,.2f}"}

        user.wallet_balance -= charge_amount
        db.session.commit()

        result = process_betting_topup(platform, account_id, float(amount), provider_phone)
        success = settle_transaction(user, result, charge_amount, "BETTING", account_id, f"{platform} betting top-up")
        if success and result.get("status") == "SUCCESS":
            tx = Transaction.query.filter_by(reference=result["reference"]).first()
            if tx is not None:
                tx.provider_name = str(result.get("provider") or tx.provider_name or "unknown").lower()[:30]
                tx.provider_reference = (result.get("provider_reference") or tx.provider_reference or result.get("reference") or "")[:100]
                db.session.commit()
            return {"status": "success", "reference": result['reference'], "message": "Betting wallet funded successfully"}
        return {"status": "error", "message": result.get("reason", "Provider failed")}

    elif name == "get_education_packages":
        from provider import fetch_education_packages
        packages = fetch_education_packages()
        if not packages:
            return {"status": "error", "message": "No packages available right now."}

        listed = []
        for pkg in packages:
            base_cost = Decimal(str(pkg["amount"]))
            cost = base_cost + get_markup("EDU", base_cost)
            listed.append(f"- {pkg.get('name')}: ₦{cost:,.2f} (System Code: {pkg.get('code')})")
        return {"status": "success", "plans_list": "\n".join(listed), "message": "Present these options to the user clearly. Do not show the System Code to the user."}

    elif name == "buy_education_pin":
        exam = kwargs.get("exam")
        try:
            quantity = int(kwargs.get("quantity", 1))
        except (TypeError, ValueError):
            quantity = 0
        amount = normalize_purchase_amount(kwargs.get("amount"))
        if quantity < 1 or quantity > 10 or amount is None:
            return {"status": "error", "message": "Enter a valid PIN quantity and price."}
        from provider import process_education_pin

        package = next(
            (item for item in fetch_education_packages() if str(item.get("code")) == str(exam)),
            None,
        )
        if not package:
            return {"status": "error", "message": "Select a valid education package."}
        base_total = Decimal(str(package.get("amount", "0"))) * quantity
        expected_amount = (base_total + get_markup("EDU", base_total)).quantize(Decimal("0.01"))
        if amount != expected_amount:
            return {"status": "error", "message": f"The PIN price has changed. Current price is NGN {expected_amount:,.2f}. Please fetch the packages again."}

        charge_amount = amount
        if user.wallet_balance < charge_amount:
            return {"status": "error", "message": f"Insufficient balance. Required: NGN {charge_amount:,.2f}"}

        user.wallet_balance -= charge_amount
        db.session.commit()

        result = process_education_pin(exam, quantity, provider_phone)
        success = settle_transaction(user, result, charge_amount, "EDU", exam, f"{exam} PIN x{quantity}")
        if success and result.get("status") == "SUCCESS":
            tx = Transaction.query.filter_by(reference=result["reference"]).first()
            if tx is not None:
                tx.provider_name = str(result.get("provider") or tx.provider_name or "unknown").lower()[:30]
                tx.provider_reference = (result.get("provider_reference") or tx.provider_reference or result.get("reference") or "")[:100]
                db.session.commit()
            return {"status": "success", "reference": result['reference'], "pins": result.get("pins", []), "message": "PIN generated successfully"}
        return {"status": "error", "message": result.get("reason", "Provider failed")}

    return {"status": "error", "message": "Unknown tool"}


def handle_chat_message(app, db, user, text, chat_id, provider_phone):
    if getattr(user, "is_escalated", False):
        lower_text = str(text).strip().lower()
        if lower_text in ["/resume_ai", "/resume", "resume ai", "reset ai"]:
            user.is_escalated = False
            db.session.commit()
            from app import send_whatsapp_message
            send_whatsapp_message(chat_id, "🤖 AI support has been resumed. How can I help you today?")
            return
        return

    if text.strip().upper().startswith("LINK "):
        from app import claim_whatsapp_link_token, send_whatsapp_message
        token = text.strip().split(None, 1)[1].strip()
        if claim_whatsapp_link_token(chat_id, token):
            send_whatsapp_message(chat_id, "✅ Your WhatsApp number is now linked to your WAJ VTU account.")
            return
        send_whatsapp_message(chat_id, "⚠️ That link is invalid or expired. Please generate a fresh link from the website dashboard.")
        return

    if requested_photo_receipt(text):
        from models import Transaction
        from app import send_whatsapp_message, send_whatsapp_receipt
        latest_transaction = Transaction.query.filter_by(
            user_id=user.id, status="SUCCESS"
        ).order_by(Transaction.created_at.desc()).first()
        if latest_transaction is None:
            send_whatsapp_message(chat_id, "I could not find a completed transaction to receipt yet.")
            return
        receipt_sent = send_whatsapp_receipt(chat_id, latest_transaction.reference)
        if receipt_sent:
            send_whatsapp_message(chat_id, "Here is your photo receipt.")
        else:
            send_whatsapp_message(chat_id, "I found the transaction, but the photo receipt service is unavailable right now.")
        return
        
    photo_receipt_requested = requested_photo_receipt(text)
    client = get_groq_client()
    if not client:
        from app import send_whatsapp_message
        send_whatsapp_message(chat_id, "AI services are currently unavailable. Please check configuration.")
        return
        
    # Keep the complete transcript in Supabase and build a safe recent context for Groq.
    state_data = user.state_data if isinstance(user.state_data, dict) else {}
    history = state_data.get("messages", [])
    if not isinstance(history, list):
        history = []
    messages = build_context(history)
    messages.append({"role": "user", "content": text})
    turn_start = len(messages) - 1
    if not history or not any(message.get("role") == "system" for message in history if isinstance(message, dict)):
        history = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    history.append({"role": "user", "content": text})
    user.state_data = {"messages": history}
    db.session.commit()
    tools = define_tools()
    
    try:
        response = client.chat.completions.create(
            messages=messages,
            model="openai/gpt-oss-120b",
            temperature=0,
            tools=tools,
            tool_choice="auto",
            max_tokens=700
        )
        
        response_message = response.choices[0].message
        
        # Loop for tool execution if AI decides to call tools
        while response_message.tool_calls:
            msg_dump = response_message.model_dump(exclude_none=True)
            messages.append(msg_dump)
            follow_up_names = set()
            
            for tool_call in response_message.tool_calls:
                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)
                
                print(f"[Agent] Calling tool: {func_name} with args {func_args}")
                
                # Quick feedback to user for slow tasks
                from app import send_whatsapp_message
                if func_name in ["buy_data", "buy_airtime", "buy_cable", "pay_electricity"]:
                    send_whatsapp_message(chat_id, "⏳ Processing your transaction...")
                elif func_name in ["get_data_plans", "get_cable_plans", "verify_meter"]:
                    send_whatsapp_message(chat_id, "🔍 Checking with the provider...")
                
                result = execute_tool(app, db, user, provider_phone, func_name, func_args)
                print(f"[Agent] Tool result: {result}")

                receipt_reference = result.get("reference")
                if func_name == "get_transaction_history" and result.get("status") == "success":
                    transactions = result.get("transactions") or []
                    receipt_reference = transactions[0].get("reference") if transactions else None
                if photo_receipt_requested and receipt_reference:
                    from app import send_whatsapp_receipt
                    threading.Thread(
                        target=send_whatsapp_receipt,
                        args=(chat_id, receipt_reference),
                        daemon=True,
                    ).start()

                if func_name == "get_data_plans" and result.get("status") == "success":
                    final_text = result["display_plans"]
                    messages.append({"role": "assistant", "content": final_text})
                    send_whatsapp_message(chat_id, final_text)
                    history.extend(messages[turn_start + 1:])
                    user.state_data = {"messages": history}
                    db.session.commit()
                    return
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": func_name,
                    "content": json.dumps(result)
                })

                follow_up_names.update({
                    "get_data_plans": {"buy_data"},
                    "get_cable_plans": {"buy_cable"},
                    "verify_meter": {"pay_electricity"},
                    "verify_betting": {"buy_betting"},
                    "get_education_packages": {"buy_education_pin"},
                }.get(func_name, set()))

            follow_up_tools = [
                tool for tool in tools
                if tool["function"]["name"] in follow_up_names
            ]
                
            response = client.chat.completions.create(
                messages=messages,
                model="openai/gpt-oss-120b",
                temperature=0,
                tools=tools,
                tool_choice="auto",
                max_tokens=700
            )
            response_message = response.choices[0].message
            
        # Final textual response
        final_text = clean_whatsapp_text(response_message.content)
        if final_text:
            messages.append({"role": "assistant", "content": final_text})
            
            from app import send_whatsapp_message
            send_whatsapp_message(chat_id, final_text)
            
        # Save only the generated part of this turn; older history remains intact.
        history.extend(messages[turn_start + 1:])
        user.state_data = {"messages": history}
        db.session.commit()
        
    except Exception as e:
        print(f"[Agent] Error: {e}")
        from app import send_whatsapp_message
        send_whatsapp_message(chat_id, "I'm having trouble processing that right now. Please try again.")
