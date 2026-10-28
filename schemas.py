from datetime import date
from typing import Any, Literal, Optional, List
from pydantic import BaseModel, Field, SecretStr, field_validator

EventType = Literal["wedding", "birthday", "community", "other"]
Priority = Literal["must", "should", "nice"]
IdeaType = Literal["theme", "menu", "decor", "entertainment", "favours"]
IdeaTier = Literal["budget", "standard", "premium"]
Language = Literal["en", "hi", "mr"]


class EventInfo(BaseModel):
    title: str = Field(description="Descriptive title of the celebration")
    type: EventType = Field(description="Type of event: wedding, birthday, community, or other")
    date: str = Field(description="Estimated event date in ISO format YYYY-MM-DD")
    city: str = Field(description="City or venue location in India")
    guest_count: int = Field(ge=1, description="Number of expected guests/attendees")
    total_budget_inr: int = Field(ge=1, description="Total event budget in Indian Rupees (INR)")
    cultural_style: str = Field(description="Tradition or regional style, e.g. Maharashtrian, Punjabi, South Indian, Contemporary")
    theme: str = Field(description="Aesthetic theme or color palette")
    summary: str = Field(description="Warm executive summary of the celebration plan")

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        date.fromisoformat(value)
        if len(value) != 10:
            raise ValueError("Date must use YYYY-MM-DD format")
        return value


class BudgetItem(BaseModel):
    category: str = Field(description="Budget category, e.g. Catering & Sweets, Venue, Decor & Florals, Photography, Entertainment")
    percentage: float = Field(description="Allocated percentage of total budget (0-100)")
    amount_inr: int = Field(description="Calculated amount in INR")
    rationale: str = Field(description="Why this amount is allocated based on guest count and traditions")
    priority: Priority = Field(description="Priority level: must, should, or nice")


class TimelineTask(BaseModel):
    id: str = Field(description="Unique task id like task-1, task-2")
    task: str = Field(description="Specific actionable task")
    category: str = Field(description="Category of the task, e.g. Venue, Catering, Invitations, Rituals")
    due_date: str = Field(description="Calculated due date in ISO format YYYY-MM-DD")
    days_before_event: int = Field(ge=0, description="Number of days before the event this task should be completed")
    depends_on: List[str] = Field(description="IDs of tasks that must be done first")
    status: Literal["todo", "done"] = Field(description="Status of the task: todo or done")
    owner: str = Field(description="Suggested owner: e.g. Host, Family Lead, Event Coordinator")

    @field_validator("due_date")
    @classmethod
    def validate_due_date(cls, value: str) -> str:
        date.fromisoformat(value)
        if len(value) != 10:
            raise ValueError("Due date must use YYYY-MM-DD format")
        return value


class VendorGuide(BaseModel):
    category: str = Field(description="Vendor category only, never invent specific names/phone numbers")
    what_to_book: str = Field(description="Exact deliverables to secure")
    quantity_or_scope: str = Field(description="Scope based on guest count, e.g. 'Buffet for 300 pax + 3 live counters'")
    estimated_cost_inr: int = Field(description="Estimated market rate in INR for this city and scale")
    questions_to_ask: List[str] = Field(description="Crucial questions to ask before signing or paying advance")
    red_flags: List[str] = Field(description="Warning signs or hidden charges to look out for")
    negotiation_tip: str = Field(description="Insider Indian negotiation tactic or value-add request")
    book_by_date: str = Field(description="Recommended deadline to lock this vendor (ISO date)")


class Idea(BaseModel):
    type: IdeaType = Field(description="Category of idea: theme, menu, decor, entertainment, favours")
    title: str = Field(description="Catchy title of the idea")
    description: str = Field(description="Creative cultural description and execution detail")
    tier: IdeaTier = Field(description="Cost tier: budget, standard, or premium")
    estimated_cost_inr: int = Field(description="Estimated incremental cost in INR")


class EventPlan(BaseModel):
    event: EventInfo
    budget: List[BudgetItem]
    timeline: List[TimelineTask]
    vendors: List[VendorGuide]
    ideas: List[Idea]
    risks: List[str] = Field(description="Potential risks, e.g., monsoon, auspicious date vendor shortages, guest surge")
    assumptions: List[str] = Field(description="Planning assumptions made regarding food choices, venue style, timings")


class CriticIssue(BaseModel):
    severity: Literal["low", "medium", "high"]
    message: str
    fix_suggestion: str


class CriticReport(BaseModel):
    passed: bool
    issues: List[CriticIssue]


class PlanResponse(BaseModel):
    needs_clarification: bool = Field(description="True if critical info (event type, date/timeframe, guest count, budget) is missing")
    questions: List[str] = Field(description="At most 2 short clarifying questions if critical info is missing, else empty list")
    plan: Optional[EventPlan] = Field(description="The complete event plan if info is sufficient, else null")


class PlanRequest(BaseModel):
    description: str
    language: Language = "en"
    clarification_answers: Optional[dict[str, str]] = None


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=1500)


class ChatRequest(BaseModel):
    message: str
    plan: EventPlan
    language: Language = "en"
    history: List[ChatMessage] = Field(default_factory=list, max_length=30)


class PlanChange(BaseModel):
    field: str
    before: Any
    after: Any


class ChatResponse(BaseModel):
    reply: str
    updated_plan: Optional[EventPlan] = None
    changes: List[PlanChange] = Field(default_factory=list)
    critic_report: Optional[CriticReport] = None
    fixed_issues: int = 0


class CalendarSyncRequest(BaseModel):
    plan: EventPlan
    google_access_token: SecretStr

