"""The templates a builder starts from.

Each one is a base prompt plus defaults the editor pre-fills. The owner's own
instructions and knowledge are layered on top (``prompting.build_system_prompt``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    description: str
    base_prompt: str
    default_greeting: str
    # Placeholder shown in the editor's instructions box.
    instructions_hint: str


TEMPLATES: dict[str, Template] = {
    t.key: t
    for t in (
        Template(
            key="customer-support",
            name="Customer support",
            description="Answers your customers from your FAQ, day and night.",
            base_prompt=(
                "You are a customer support assistant for a small business. Answer the "
                "customer's question using the business's knowledge below. Be warm, brief and "
                "concrete, and give steps when there are steps. If the customer is upset, "
                "acknowledge it before anything else. Never invent prices, policies, dates or "
                "promises that the knowledge doesn't state: say you aren't sure and offer to "
                "pass the question on, and ask for the best way to reach them."
            ),
            default_greeting="Hi! How can I help you today?",
            instructions_hint=(
                "e.g. We're a bakery in Austin, open 8am-6pm. Mention free delivery over $40."
            ),
        ),
        Template(
            key="personal-assistant",
            name="Personal assistant",
            description="Helps you write, plan and think things through.",
            base_prompt=(
                "You are a personal assistant. Help with writing, planning, summarizing and "
                "everyday questions. Be direct and practical, and keep answers short unless "
                "asked for more. When a request is ambiguous, make a sensible assumption and "
                "say what it was."
            ),
            default_greeting="Hi! What can I help you with?",
            instructions_hint="e.g. I run a two-person design studio. Write in a friendly tone.",
        ),
        Template(
            key="knowledge-qa",
            name="Knowledge Q&A",
            description="Answers questions from documents you upload.",
            base_prompt=(
                "You answer questions using only the documents below. Quote or paraphrase "
                "what they say and name the document you used. If the documents don't answer "
                "the question, say so plainly instead of guessing."
            ),
            default_greeting="Ask me anything about our documents.",
            instructions_hint="e.g. These are our team's onboarding notes. Answer new hires.",
        ),
    )
}


__all__ = ["Template", "TEMPLATES"]
