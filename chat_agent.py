import os
import json
from decimal import Decimal
from datetime import datetime
from groq import Groq

def get_groq_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    return Groq(api_key=api_key)

SYSTEM_PROMPT = """You are WAJ VTU Assistant, a helpful AI that allows users in Nigeria to buy Data, Airtime, Cable TV, Electricity, Betting Top-ups, and Education PINs.
You have access to tools to fetch plans and execute transactions. 

RULES:
1. When a user asks for a service, FIRST use the fetching tool to see available plans and their EXACT `plan_code` and `amount`.
2. Present options nicely formatted with prices.
3. When confirmed, use the purchase tool with the exact `plan_code` and `amount`.
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
                        "phone": {"type": "string", "description": "The 11-digit recipient phone number"}
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
                "name": "generate_topup_link",
                "description": "Generate a payment link to fund the user's wallet.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "number", "description": "Amount to fund in Naira"},
                        "email": {"type": "string", "description": "User's email address"}
                    },
                    "required": ["amount", "email"]
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
        verify_meter as provider_verify_meter, process_electricity_payment
    )
    from app import get_markup, settle_transaction, generate_payment_link, Transaction, ScheduledTask
    

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
        for plan in variations:
            base_cost = Decimal(str(plan.get("variation_amount")))
            cost = base_cost + get_markup(f"DATA_{network}", base_cost)
            plans.append({
                "name": plan.get("name"),
                "plan_code": plan.get("variation_code"),
                "amount": float(cost)
            })
        return {"status": "success", "plans": plans}
        
    elif name == "buy_data":
        network = kwargs.get("network")
        plan_code = kwargs.get("plan_code")
        amount = Decimal(str(kwargs.get("amount")))
        phone = kwargs.get("phone")
        
        if user.wallet_balance < amount:
            return {"status": "error", "message": f"Insufficient balance. Wallet balance is NGN {user.wallet_balance:,.2f}"}
            
        user.wallet_balance -= amount
        db.session.commit()
        
        result = process_data_purchase(phone, network, plan_code, float(amount))
        if result.get("status") == "SUCCESS":
            tx = Transaction(
                user_id=user.id,
                reference=result['reference'],
                amount=amount,
                type='DATA',
                recipient=phone,
                status='SUCCESS',
                description=f"{network} {plan_code} to {phone}"
            )
            db.session.add(tx)
            db.session.commit()
            return {"status": "success", "reference": result['reference'], "message": f"Successfully purchased data for {phone}"}
        else:
            user.wallet_balance += amount
            db.session.commit()
            return {"status": "error", "message": result.get("reason", "Provider failed")}

    elif name == "buy_airtime":
        network = kwargs.get("network")
        amount = Decimal(str(kwargs.get("amount")))
        phone = kwargs.get("phone")
        
        charge_amount = amount + get_markup("AIRTIME", amount)
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
                status='SUCCESS',
                description=f"{network} Airtime to {phone}"
            )
            db.session.add(tx)
            db.session.commit()
            return {"status": "success", "reference": result['reference'], "message": f"Successfully sent NGN {amount} airtime to {phone}"}
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
            plans.append({
                "name": plan.get("name"),
                "plan_code": plan.get("code"),
                "amount": float(cost)
            })
        return {"status": "success", "plans": plans}
        
    elif name == "buy_cable":
        provider = kwargs.get("provider")
        smartcard = kwargs.get("smartcard")
        plan_code = kwargs.get("plan_code")
        amount = Decimal(str(kwargs.get("amount")))
        
        if user.wallet_balance < amount:
            return {"status": "error", "message": f"Insufficient balance. Wallet balance is NGN {user.wallet_balance:,.2f}"}
            
        verification = verify_smartcard(provider, smartcard)
        if not verification.get("valid"):
            return {"status": "error", "message": verification.get("message", "Invalid smartcard")}
            
        user.wallet_balance -= amount
        db.session.commit()
        
        result = process_cable_tv(provider, smartcard, plan_code, float(amount), provider_phone)
        success = settle_transaction(user, result, amount, "CABLE", smartcard, f"{provider} {plan_code}")
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
        amount = Decimal(str(kwargs.get("amount")))
        
        charge_amount = amount + get_markup("ELECTRICITY", amount)
        if user.wallet_balance < charge_amount:
            return {"status": "error", "message": f"Insufficient balance. Required: NGN {charge_amount:,.2f}"}
            
        user.wallet_balance -= charge_amount
        db.session.commit()
        
        result = process_electricity_payment(disco, meter, mtype, float(amount), provider_phone)
        success = settle_transaction(user, result, charge_amount, "ELECTRICITY", meter, f"{disco} electricity payment")
        if success:
            return {"status": "success", "reference": result['reference'], "token": result.get("token"), "message": "Payment successful"}
        else:
            return {"status": "error", "message": result.get("reason", "Payment failed")}

    elif name == "generate_topup_link":
        amount = Decimal(str(kwargs.get("amount")))
        email = kwargs.get("email")
        
        result = generate_payment_link(email, amount, provider_phone, pass_fee_to_user=True)
        if result.get("status") == "SUCCESS":
            from app import ensure_deposit_transaction
            ensure_deposit_transaction(user, result["reference"], result.get("net_amount", amount), user.phone, status="PENDING")
            return {"status": "success", "gross_amount": float(result["gross_amount"]), "payment_url": result["payment_url"]}
        else:
            return {"status": "error", "message": result.get("reason", "Could not generate link")}
            
    return {"status": "error", "message": "Unknown tool"}


def handle_chat_message(app, db, user, text, chat_id, provider_phone):
    if getattr(user, "is_escalated", False):
        return
        
    client = get_groq_client()
    if not client:
        from app import send_whatsapp_message
        send_whatsapp_message(chat_id, "AI services are currently unavailable. Please check configuration.")
        return
        
    # Get memory
    state_data = user.state_data or {}
    messages = state_data.get("messages", [])
    
    if not messages:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        
    # Truncate history to last 15 items to save tokens (system + 14 msgs)
    if len(messages) > 15:
        messages = [messages[0]] + messages[-14:]
        
    messages.append({"role": "user", "content": text})
    tools = define_tools()
    
    try:
        response = client.chat.completions.create(
            messages=messages,
            model="llama-3.1-8b-instant",
            temperature=0,
            tools=tools,
            tool_choice="auto",
            max_tokens=400
        )
        
        response_message = response.choices[0].message
        
        # Loop for tool execution if AI decides to call tools
        while response_message.tool_calls:
            msg_dump = response_message.model_dump(exclude_none=True)
            messages.append(msg_dump)
            
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
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": func_name,
                    "content": json.dumps(result)
                })
                
            response = client.chat.completions.create(
                messages=messages,
                model="llama-3.1-8b-instant",
                temperature=0,
                tools=tools,
                tool_choice="auto",
                max_tokens=400
            )
            response_message = response.choices[0].message
            
        # Final textual response
        final_text = response_message.content
        if final_text:
            messages.append({"role": "assistant", "content": final_text})
            
            from app import send_whatsapp_message
            send_whatsapp_message(chat_id, final_text)
            
        # Save memory
        user.state_data = {"messages": messages}
        db.session.commit()
        
    except Exception as e:
        print(f"[Agent] Error: {e}")
        from app import send_whatsapp_message
        send_whatsapp_message(chat_id, "I'm having trouble processing that right now. Please try again.")
