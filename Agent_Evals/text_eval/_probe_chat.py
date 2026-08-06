"""De-risk probe: key-less Azure OpenAI chat completions WITH function calling,
against FoundryResourceForDemos (gpt-4.1-mini), using Microsoft Entra (no key)."""

import os

os.environ.setdefault("AZURE_TOKEN_CREDENTIALS", "dev")

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

ENDPOINT = "https://foundryresourcefordemos.cognitiveservices.azure.com/"
API_VERSION = "2024-10-21"
MODEL = "gpt-4.1-mini"

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
)
client = AzureOpenAI(
    azure_endpoint=ENDPOINT, azure_ad_token_provider=token_provider, api_version=API_VERSION
)

tools = [
    {
        "type": "function",
        "function": {
            "name": "submit_step_result",
            "description": "Submit one answer for the backend's current savings-application state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {"type": "string", "enum": ["CONFIRMED", "AVAILABLE", "CAPTURED"]},
                    "value": {"type": "string"},
                },
                "required": ["result"],
            },
        },
    }
]

resp = client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "system", "content": "You are a savings-account assistant. If the customer confirms their identity, call submit_step_result with result=CONFIRMED."},
        {"role": "user", "content": "Yes, this is Priya speaking."},
    ],
    tools=tools,
    tool_choice="auto",
    temperature=0.3,
)
msg = resp.choices[0].message
print("finish_reason:", resp.choices[0].finish_reason)
if msg.tool_calls:
    for tc in msg.tool_calls:
        print("TOOL CALL:", tc.function.name, tc.function.arguments)
else:
    print("TEXT:", msg.content)
print("OK — key-less chat + tools works")
