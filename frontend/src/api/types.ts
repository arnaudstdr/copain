// ── Types des payloads HTTP ─────────────────────────────────────────────────
// Miroir 1:1 des modèles Pydantic de `bot/api.py`. Toute divergence de forme
// est un bug (migration iso-fonctionnelle). Les dates sont des chaînes ISO 8601
// (le backend sérialise en `str`), jamais des `Date` — le formatage est côté UI.

// Config publique servie sans auth (réseau Tailscale privé).
export interface ConfigResponse {
  api_key: string;
}

// Intent décidé par le LLM via le bloc <meta>.
export type Intent =
  | "answer"
  | "task"
  | "search"
  | "memory"
  | "feed"
  | "event"
  | "fuel"
  | "weather"
  | "depot"
  | "expense";

// ── /ask, /ask/image ────────────────────────────────────────────────────────

// Brouillon de dépense extrait d'une capture (Revolut) via vision, SANS écriture.
// La PWA pré-remplit le formulaire Budget ; l'écriture réelle passe par POST /expenses.
export interface ExpenseDraft {
  action: string;
  amount_eur: number | null;
  label: string | null;
  category: string | null;
  occurred_on: string | null;
  shared: boolean;
  recurring_key: string | null;
}

// Action concrète proposée par copain, rendue en bouton tappable (miroir du
// modèle Pydantic `Action`). `open` = deep-link construit côté serveur ; un tap
// l'ouvre (jamais d'exécution automatique).
export interface Action {
  type: string;
  label: string;
  open: string;
}

export interface AskResponse {
  response: string;
  intent: string;
  refresh_cards: string[];
  actions?: Action[];
  expense_draft: ExpenseDraft | null;
}

// ── /notifications ───────────────────────────────────────────────────────────

export interface NotificationItem {
  id: number;
  text: string;
  created_at: string;
}

export interface NotificationsResponse {
  notifications: NotificationItem[];
}

// ── /dashboard ────────────────────────────────────────────────────────────────

export interface WeatherCard {
  city: string;
  temp_current: number;
  temp_min: number;
  temp_max: number;
  description: string;
  precipitation_mm: number;
  wind_kmh: number;
}

export interface NextEventCard {
  title: string;
  start: string;
  end: string;
  location: string | null;
  calendar_name: string;
  actions?: Action[];
}

export interface TaskCard {
  id: number;
  content: string;
  due_at: string | null;
}

export interface BudgetEnvelopeCard {
  category: string;
  label: string;
  allocated_eur: number;
  spent_eur: number;
  remaining_eur: number; // peut être négatif si dépassement
  is_overrun: boolean;
  shared: boolean; // True → compte joint, purement informatif
}

export interface BudgetCard {
  month: string; // ISO date du 1er du mois (YYYY-MM-DD)
  income_eur: number;
  spent_eur: number;
  remaining_eur: number; // prévisionnel
  saved_this_year_eur: number;
  pending_recurring_count: number;
  has_overdue: boolean;
  envelopes: BudgetEnvelopeCard[];
  has_envelope_overrun: boolean;
}

export interface DashboardResponse {
  weather: WeatherCard | null;
  next_event: NextEventCard | null;
  today_tasks: TaskCard[];
  overdue_tasks: number;
  unread_notifications: number;
  budget: BudgetCard | null;
}

// ── /tasks ────────────────────────────────────────────────────────────────────

export interface TasksListResponse {
  tasks: TaskCard[];
}

export interface TaskMutationResponse {
  ok: boolean;
}

// ── /thoughts (décharge cognitive) ─────────────────────────────────────────────

export type ThoughtKind = "worry" | "idea" | "note";

export interface ThoughtItem {
  id: number;
  content: string;
  kind: string | null;
  created_at: string; // ISO
  closed: boolean;
}

export interface ThoughtsListResponse {
  thoughts: ThoughtItem[];
}

export interface ThoughtCloseResponse {
  closed: boolean;
  thought_id: number;
}

export interface ThoughtCreateRequest {
  content: string;
  kind?: ThoughtKind | null;
}

export interface ThoughtCreateResponse {
  recorded: boolean;
  thought: ThoughtItem;
  ack: string; // accusé sobre (+ suffixe boucle si rumination détectée)
}

// ── /history (mode dialogue) ────────────────────────────────────────────────────

export interface ChatMessageItem {
  id: number;
  role: string; // "user" | "assistant"
  content: string;
  created_at: string; // ISO 8601
}

export interface ChatHistoryResponse {
  messages: ChatMessageItem[]; // ordre chronologique croissant
  has_more: boolean; // curseur = messages[0].id
}

// ── /news/latest (card Actu) ────────────────────────────────────────────────────

export interface NewsLatestResponse {
  markdown: string;
  fetched_at: string; // ISO 8601 UTC
}

// ── /foryou (restitution des dépôts) ────────────────────────────────────────────

export interface ForYouItemResponse {
  type: string; // closable_worry | loop | stale_idea
  message: string;
  thought_ids: number[];
}

export interface ForYouResponse {
  items: ForYouItemResponse[];
  fetched_at: string; // ISO 8601 UTC
}

// ── /budget, /expenses ───────────────────────────────────────────────────────────

export interface BudgetTransaction {
  id: number;
  kind: string; // punctual | recurring_tick | saving_tick | income
  amount_eur: number;
  label: string;
  category: string | null;
  recurring_key: string | null;
  occurred_on: string; // ISO date
  shared: boolean; // True → compte joint, hors restant perso
}

export interface BudgetPendingItem {
  key: string;
  label: string;
  amount_eur: number;
  day: number;
  kind: string; // expense | saving
  is_overdue: boolean;
}

export interface BudgetEnvelopeDetail {
  category: string;
  label: string;
  allocated_eur: number;
  spent_eur: number;
  remaining_eur: number;
  overrun_eur: number;
  is_overrun: boolean;
  shared: boolean; // True → compte joint, hors restant perso
}

export interface SpendPoint {
  date: string; // ISO date — un point par jour écoulé du cycle
  cumulative_eur: number; // cumul des ponctuelles perso (enveloppes incluses, shared exclu)
}

export interface BudgetMonthDetail {
  month: string; // ISO date du début de cycle
  cycle_start: string; // ISO date — début du cycle (inclus)
  cycle_end: string; // ISO date — dernier jour du cycle (inclus)
  spend_horizon: string; // ISO date — fin de cycle visée par la projection/sparkline (horizon)
  currency: string;
  income_eur: number;
  spent_punctual_eur: number;
  spent_recurring_eur: number;
  saved_this_month_eur: number;
  saved_this_year_eur: number;
  remaining_eur: number;
  projected_remaining_eur: number; // projection fin de cycle (rythme extrapolé)
  daily_rate_eur: number; // rythme quotidien constaté (0 au jour du salaire)
  spendable_eur: number; // cible « rythme idéal » = income - récurrentes - épargne
  transactions: BudgetTransaction[];
  pending: BudgetPendingItem[];
  envelopes: BudgetEnvelopeDetail[];
  spend_curve: SpendPoint[];
}

export interface CoursesShareCard {
  text: string;
  label: string;
  remaining_eur: number;
  allocated_eur: number;
  spent_eur: number;
  is_overrun: boolean;
  as_of: string; // ISO date du jour de calcul
}

export type ExpenseAction = "spend" | "income" | "tick_recurring";

export interface ExpenseCreate {
  action: ExpenseAction;
  amount_eur?: number | null;
  label?: string | null;
  category?: string | null;
  occurred_on?: string | null; // ISO YYYY-MM-DD ; null → aujourd'hui
  shared?: boolean; // spend uniquement
  recurring_key?: string | null; // tick uniquement
  starts_cycle?: boolean; // income uniquement : ancre un nouveau cycle
}

export interface ExpenseCreateResponse {
  // recorded=false → tick de récurrente déjà pointé (idempotent), transaction=null
  recorded: boolean;
  transaction: BudgetTransaction | null;
}

// Édition partielle d'une écriture (PATCH /expenses/{id}, miroir d'ExpenseUpdate
// Pydantic). Tous champs optionnels (sémantique PATCH) ; `kind`/`recurring_key`
// ne sont pas éditables (corriger un kind = supprimer + recréer). La réponse est
// la transaction mise à jour (BudgetTransaction).
export interface ExpenseUpdate {
  amount_eur?: number | null;
  label?: string | null;
  category?: string | null;
  occurred_on?: string | null; // ISO YYYY-MM-DD
  shared?: boolean | null;
}

// ── /weather/forecast ───────────────────────────────────────────────────────────

export interface HourlyForecastItem {
  time: string; // ISO
  temp_c: number;
  precipitation_mm: number;
  precipitation_probability_pct: number;
  description: string;
}

export interface DailyForecastItem {
  date: string; // ISO date
  temp_min: number;
  temp_max: number;
  temp_current: number | null;
  precipitation_mm: number;
  wind_kmh_max: number;
  description: string;
}

export interface WeatherForecastResponse {
  city: string;
  hourly: HourlyForecastItem[]; // 24h glissantes
  daily: DailyForecastItem[]; // 7 prochains jours
}

// ── /events (agenda iCloud) ─────────────────────────────────────────────────────

export interface CalendarEventItem {
  uid: string;
  title: string;
  start: string; // ISO
  end: string;
  location: string | null;
  description: string | null;
  calendar_name: string;
  actions?: Action[];
}

export interface EventsListResponse {
  events: CalendarEventItem[];
}

// ── /event/location ──────────────────────────────────────────────────────────────

export interface LocationEventRequest {
  event: "arrived" | "left";
  place: string;
  lat?: number | null;
  lon?: number | null;
  at?: string | null; // ISO 8601 ; null → now() serveur
}

export interface LocationEventResponse {
  recorded: boolean;
  current_place: string | null;
}

// ── SSE /ask/stream ───────────────────────────────────────────────────────────────
// Frames `data: {json}\n\n` émises par process_message_stream (cf. bot/api.py).

// Body de POST /ask/stream (miroir de bot.api.AskRequest). `think` override le
// mode réflexion pour ce message (toggle du chat) ; absent = défaut OLLAMA_THINK.
export interface AskStreamRequest {
  message: string;
  think?: boolean;
}

export interface StreamDelta {
  type: "delta";
  text: string;
}

export interface StreamReplace {
  type: "replace";
  text: string;
}

export interface StreamDone {
  type: "done";
  intent: string;
  refresh_cards: string[];
  actions?: Action[];
}

export interface StreamErrorEvent {
  type: "error";
  text: string;
}

export type StreamFrame = StreamDelta | StreamReplace | StreamDone | StreamErrorEvent;

// Handlers de consommation du flux SSE (consommés au step 07).
export interface StreamHandlers {
  onDelta: (text: string) => void;
  onReplace: (text: string) => void;
  onDone: (intent: string, refreshCards: string[], actions: Action[]) => void;
  onError: (text: string) => void;
}
