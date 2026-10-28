export type Language = "en" | "hi" | "mr";

export interface EventInfo {
  title: string;
  type: string;
  date: string;
  city: string;
  guest_count: number;
  total_budget_inr: number;
  cultural_style: string;
  theme: string;
  summary: string;
}

export interface BudgetItem {
  category: string;
  percentage: number;
  amount_inr: number;
  rationale: string;
  priority: string;
}

export interface TimelineTask {
  id: string;
  task: string;
  category: string;
  due_date: string;
  days_before_event: number;
  depends_on: string[];
  status: "todo" | "done";
  owner: string;
}

export interface VendorGuide {
  category: string;
  what_to_book: string;
  quantity_or_scope: string;
  estimated_cost_inr: number;
  questions_to_ask: string[];
  red_flags: string[];
  negotiation_tip: string;
  book_by_date: string;
}

export interface Idea {
  type: string;
  title: string;
  description: string;
  tier: string;
  estimated_cost_inr: number;
}

export interface EventPlan {
  event: EventInfo;
  budget: BudgetItem[];
  timeline: TimelineTask[];
  vendors: VendorGuide[];
  ideas: Idea[];
  risks: string[];
  assumptions: string[];
}

export interface CriticIssue {
  severity: "low" | "medium" | "high";
  message: string;
  fix_suggestion: string;
}

export interface CriticReport {
  passed: boolean;
  issues: CriticIssue[];
}

export interface PlanChange {
  field: string;
  before: unknown;
  after: unknown;
}

export interface ChatEntry {
  role: "user" | "assistant";
  content: string;
}
