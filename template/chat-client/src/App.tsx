import React, { useState, useRef, useEffect, useCallback } from 'react';
import axios from 'axios';
import './App.css';

const AGENT_URL = process.env.REACT_APP_AGENT_URL || 'http://localhost:10999';
const WS_URL = AGENT_URL.replace(/^http/, 'ws') + '/ws/trace';

// ── Types ──────────────────────────────────────────────────────────────────────
interface Message {
  id: string;
  role: 'user' | 'agent';
  text: string;
  checkout?: CheckoutData;
}

interface CheckoutData {
  id: string;
  state: 'NOT_READY_FOR_PAYMENT' | 'READY_FOR_PAYMENT' | 'COMPLETED';
  line_items: LineItem[];
  subtotal: number;
  shipping: number | null;
  total: number;
  payment_instruments: PaymentInstrument[];
  fulfillment_options: FulfillmentOption[];
  order_id?: string;
  ap2_token?: AP2Token;
}

interface LineItem { name: string; quantity: number; unit_price: number; product_id: string; image?: string; }
interface PaymentInstrument { type: string; label: string; }
interface FulfillmentOption { id: string; label: string; price: number; days: string; }

interface AP2Token {
  token_id: string; sub: string; intent: string; merchant_scope: string;
  max_amount: number; currency: string; expires_at: string;
  single_use: boolean; revocation_url: string; user_consent_proof: string;
}

interface ProtocolEvent {
  id: string;
  type: 'mcp' | 'a2a' | 'ucp' | 'acp' | 'payment' | 'rest';
  title: string;
  intent: string;
  data: object;
  timestamp: string;
}

interface TraceEvent {
  timestamp: number;
  type: 'mcp' | 'a2a' | 'ucp' | 'system';
  tool?: string;
  event?: string;
  latency_ms?: number;
  task_id?: string;
  input?: object;
  output?: object;
  message?: string;
  [key: string]: unknown;
}

// ── Helpers ────────────────────────────────────────────────────────────────────
const ts = () => new Date().toLocaleTimeString('en-US', { hour12: false });
const uid = () => Math.random().toString(36).slice(2, 10);

function prettyJson(obj: object): string {
  return JSON.stringify(obj, null, 2);
}

// ── JSON colorizer ─────────────────────────────────────────────────────────────
function ColorJson({ data }: { data: object }) {
  const raw = prettyJson(data);
  const html = raw
    .replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
      (match) => {
        let cls = 'json-num';
        if (/^"/.test(match)) { cls = /:$/.test(match) ? 'json-key' : 'json-str'; }
        else if (/true|false/.test(match)) { cls = 'json-bool'; }
        else if (/null/.test(match)) { cls = 'json-null'; }
        const colors: Record<string, string> = {
          'json-key': '#9D4EDD', 'json-str': '#00F5FF',
          'json-num': '#FF6EC7', 'json-bool': '#FFD700', 'json-null': '#9B89C4'
        };
        return `<span style="color:${colors[cls] || '#F0E6FF'}">${match}</span>`;
      });
  return <div className="json-block" dangerouslySetInnerHTML={{ __html: html }} />;
}

// ── Protocol Event Card ────────────────────────────────────────────────────────
function EventCard({ ev }: { ev: ProtocolEvent }) {
  const [open, setOpen] = useState(true);
  const tagClass: Record<string, string> = {
    mcp: 'tag-mcp', a2a: 'tag-a2a', ucp: 'tag-ucp',
    acp: 'tag-acp', payment: 'tag-pay', rest: 'tag-rest'
  };
  return (
    <div className="event-card">
      <div className="event-header" onClick={() => setOpen(o => !o)}>
        <span className={`event-tag ${tagClass[ev.type]}`}>{ev.type.toUpperCase()}</span>
        <span className="event-title">{ev.title}</span>
        <span className="event-ts">{ev.timestamp}</span>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.7rem', marginLeft: 4 }}>{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <div className="event-body">
          <div className="intent-label">💡 {ev.intent}</div>
          <ColorJson data={ev.data} />
        </div>
      )}
    </div>
  );
}

// ── Checkout Card ──────────────────────────────────────────────────────────────
function CheckoutCard({ checkout, onUpdate }: { checkout: CheckoutData; onUpdate: (c: CheckoutData) => void }) {
  const [selectedPayment, setSelectedPayment] = useState('');
  const [selectedShipping, setSelectedShipping] = useState('standard');
  const [loading, setLoading] = useState(false);
  const [orderConfirmed, setOrderConfirmed] = useState(checkout.state === 'COMPLETED');

  const stateClass: Record<string, string> = {
    NOT_READY_FOR_PAYMENT: 's-incomplete', READY_FOR_PAYMENT: 's-ready', COMPLETED: 's-completed'
  };

  const handleComplete = async () => {
    setLoading(true);
    try {
      const r = await axios.post(`${AGENT_URL}/ucp/checkout/${checkout.id}/complete`, {
        shipping_address: { line1: '123 Demo St', city: 'Minneapolis', state: 'MN', zip: '55401', country: 'US' },
        payment_instrument: selectedPayment || 'visa',
        fulfillment_option: selectedShipping,
      });
      onUpdate({ ...checkout, ...r.data, state: 'READY_FOR_PAYMENT' });
    } catch {}
    setLoading(false);
  };

  const handleConfirm = async () => {
    setLoading(true);
    try {
      const r = await axios.post(`${AGENT_URL}/ucp/checkout/${checkout.id}/confirm`, {});
      const updated = { ...checkout, ...r.data, state: 'COMPLETED' as const };
      onUpdate(updated);
      setOrderConfirmed(true);
    } catch {}
    setLoading(false);
  };

  return (
    <div className="checkout-card">
      <h4> UCP Checkout — My Store</h4>
      <div className={`checkout-state-badge ${stateClass[checkout.state]}`}>{checkout.state}</div>
      {checkout.line_items.map((item, i) => (
        <div className="line-item" key={i}>
          <span>{item.image || ''} {item.name} × {item.quantity}</span>
          <span>${(item.unit_price * item.quantity).toFixed(2)}</span>
        </div>
      ))}
      {checkout.shipping !== null && (
        <div className="line-item"><span>Shipping</span><span>${checkout.shipping?.toFixed(2)}</span></div>
      )}
      <div className="checkout-total">
        <span>Total</span><span>${checkout.total.toFixed(2)}</span>
      </div>

      {checkout.state === 'NOT_READY_FOR_PAYMENT' && !orderConfirmed && (
        <>
          <div style={{ marginTop: 12, fontSize: '0.72rem', color: 'var(--text-muted)' }}>Shipping:</div>
          <select className="fulfillment-select" value={selectedShipping} onChange={e => setSelectedShipping(e.target.value)}>
            {checkout.fulfillment_options.map(f => (
              <option key={f.id} value={f.id}>{f.label} (+${f.price}) — {f.days} days</option>
            ))}
          </select>
          <div style={{ marginTop: 8, fontSize: '0.72rem', color: 'var(--text-muted)' }}>Payment:</div>
          <div className="pay-options">
            {checkout.payment_instruments.map(p => (
              <button key={p.type} className={`pay-btn${selectedPayment === p.type ? ' selected' : ''}`}
                onClick={() => setSelectedPayment(p.type)}>{p.label}</button>
            ))}
          </div>
          <button className="confirm-btn" disabled={!selectedPayment || loading} onClick={handleComplete}>
            {loading ? 'Processing...' : '📦 Proceed to Payment →'}
          </button>
        </>
      )}

      {checkout.state === 'READY_FOR_PAYMENT' && !orderConfirmed && (
        <>
          <div style={{ marginTop: 12, fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: 8 }}>
            🔐 An agentic payment token (AP2 mandate) will be issued to authorize this purchase.
          </div>
          <button className="confirm-btn" disabled={loading} onClick={handleConfirm}>
            {loading ? 'Authorizing...' : '🔐 Confirm & Issue AP2 Token →'}
          </button>
        </>
      )}

      {(orderConfirmed || checkout.state === 'COMPLETED') && (
        <div className="order-confirmed" style={{ marginTop: 12 }}>
          ✅ Order {checkout.order_id} confirmed!
        </div>
      )}
    </div>
  );
}

// ── Token Visualizer ───────────────────────────────────────────────────────────
function TokenVisualizer({ token }: { token: AP2Token }) {
  const fields: Array<{ key: keyof AP2Token; why: string }> = [
    { key: 'sub',               why: 'Who authorized this agent to buy' },
    { key: 'intent',            why: 'Exactly what the agent is buying — scoped to the purchase context' },
    { key: 'merchant_scope',    why: 'Agent can only spend at this specific merchant' },
    { key: 'max_amount',        why: 'Hard cap — agent cannot exceed this amount' },
    { key: 'currency',          why: 'Currency for the mandate' },
    { key: 'expires_at',        why: 'Token self-destructs after this time' },
    { key: 'single_use',        why: 'Can only be used once — prevents replay attacks' },
    { key: 'revocation_url',    why: 'User or platform can kill this token instantly' },
    { key: 'user_consent_proof',why: 'Cryptographic proof a human authorized this purchase' },
  ];
  return (
    <div className="token-vis">
      <h3>🔐 AP2 Agentic Payment Token — Decoded</h3>
      <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginBottom: 12, fontFamily: 'JetBrains Mono' }}>
        token_id: {token.token_id}
      </div>
      <div className="tf-grid">
        <div className="tf-header">Field</div>
        <div className="tf-header">Value</div>
        <div className="tf-header">Why it matters</div>
        {fields.map(f => (
          <React.Fragment key={String(f.key)}>
            <div className="tf-key">{String(f.key)}</div>
            <div className="tf-val">{String(token[f.key])}</div>
            <div className="tf-why">{f.why}</div>
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

// ── Timeline Item ──────────────────────────────────────────────────────────────
function TimelineItem({ ev }: { ev: TraceEvent }) {
  const [open, setOpen] = React.useState(false);
  const typeColor: Record<string, string> = {
    mcp: 'var(--green)', a2a: 'var(--cyan)', ucp: 'var(--violet-bright)', system: 'var(--text-muted)',
  };
  const color = typeColor[ev.type] || 'var(--text-muted)';
  const label = ev.tool || ev.event || ev.type;
  const t = new Date(ev.timestamp * 1000).toLocaleTimeString('en-US', { hour12: false });
  return (
    <div style={{ borderLeft: `2px solid ${color}`, paddingLeft: 10, marginBottom: 8, opacity: ev.type === 'system' ? 0.5 : 1 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }} onClick={() => setOpen(o => !o)}>
        <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>{t}</span>
        <span style={{ fontSize: '0.7rem', color, fontWeight: 600 }}>{ev.type.toUpperCase()}</span>
        <span style={{ fontSize: '0.72rem', color: 'var(--text-primary)', flex: 1 }}>{label}</span>
        {ev.latency_ms !== undefined && (
          <span style={{
            fontSize: '0.62rem', fontFamily: 'JetBrains Mono', padding: '1px 6px',
            borderRadius: 4,
            background: ev.latency_ms < 200 ? 'rgba(0,245,153,0.12)' : ev.latency_ms < 800 ? 'rgba(255,214,0,0.12)' : 'rgba(255,110,199,0.12)',
            color: ev.latency_ms < 200 ? 'var(--green)' : ev.latency_ms < 800 ? 'var(--gold)' : 'var(--pink)',
          }}>{ev.latency_ms}ms</span>
        )}
        <span style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <div style={{ marginTop: 6 }}>
          <ColorJson data={ev as unknown as object} />
        </div>
      )}
    </div>
  );
}

// ── Inspector Tabs ─────────────────────────────────────────────────────────────
type Tab = 'mcp' | 'a2a' | 'ucp' | 'acp' | 'payment' | 'timeline';

const ACP_SIMULATION: ProtocolEvent[] = [
  {
    id: 'acp1', type: 'acp', title: 'Step 1 — Submit Product Feed', timestamp: '--:--:--',
    intent: 'Both ACP (OpenAI) and UCP (Google) are feed-first: discovery starts with a structured product feed you submit before any checkout integration.',
    data: {
      note: 'SIMULATED — reflects public ACP + UCP documentation',
      step: 1,
      description: 'Merchant submits a structured product feed to OpenAI (chatgpt.com/merchants) and/or to Google Merchant Center. The AI surface indexes the catalog from this feed.',
      feed_record_example: {
        id: 'prod_001', title: 'Classic Tee', description: 'Soft 100% cotton t-shirt in a relaxed unisex fit',
        price: { value: 19.99, currency: 'USD' }, availability: 'in_stock',
        image_url: 'https://example.com/images/prod_001.jpg',
        native_commerce: true  // UCP: opts product into agentic checkout on Google AI surfaces
      },
      delivery_options: ['file_upload (recommended for full catalog — daily snapshot)', 'API upsert (for incremental updates and promotions)']
    }
  },
  {
    id: 'acp2', type: 'acp', title: 'Step 2 — AI Indexes & Surfaces Products', timestamp: '--:--:--',
    intent: 'ChatGPT / Gemini indexes the feed. Users discover products through the AI surface — no live product-search endpoint is called at query time.',
    data: {
      note: 'SIMULATED',
      step: 2,
      description: 'When a user asks ChatGPT or Gemini about products, the AI searches its indexed catalog (from the feed). The merchant does not receive a real-time query.',
      user_query: 'show me t-shirts under $25',
      ai_surface: 'searches own indexed catalog from submitted feed',
      result_shown_to_user: { title: 'Classic Tee', price: '$19.99', availability: 'In stock', buy_link: 'enabled (native_commerce: true)' }
    }
  },
  {
    id: 'acp3', type: 'acp', title: 'Step 3 — Checkout Initiated', timestamp: '--:--:--',
    intent: 'User clicks buy. For UCP: Google calls your 3 REST checkout endpoints. For ACP: checkout details not yet fully public — OpenAI is building this out.',
    data: {
      note: 'SIMULATED',
      step: 3,
      ucp_checkout_flow: {
        step_1: 'POST /checkout/sessions  — create session with line items',
        step_2: 'PATCH /checkout/sessions/{id} — add shipping + payment token from Google Pay wallet',
        step_3: 'POST /checkout/sessions/{id}/complete — finalize order'
      },
      payment_token_source: 'Google Pay wallet credential — NOT tied to a specific PSP. Merchant processes it with their own payment provider.'
    }
  },
  {
    id: 'acp4', type: 'acp', title: 'ACP vs UCP — Key Differences', timestamp: '--:--:--',
    intent: 'Both require a product feed. The difference is in checkout ownership and openness.',
    data: {
      acp_openai: {
        discovery: 'feed submitted to OpenAI (chatgpt.com/merchants)',
        spec: 'closed — OpenAI defines and controls it',
        checkout: 'OpenAI-managed checkout (details still being rolled out)',
        surfaces: 'ChatGPT only'
      },
      ucp_google: {
        discovery: 'product feed via Google Merchant Center (native_commerce: true attribute)',
        spec: 'open standard — ucp.dev, Apache 2.0',
        checkout: '3 REST endpoints on your own server; you stay Merchant of Record',
        payment_handler: 'Google Pay (wallet credential — PSP is your choice)',
        surfaces: 'Google AI Mode, Gemini'
      }
    }
  },
];

function InspectorPanel({ events, checkout, agentInfo, traceEvents }: {
  events: ProtocolEvent[];
  checkout: CheckoutData | null;
  agentInfo: object | null;
  traceEvents: TraceEvent[];
}) {
  const [tab, setTab] = useState<Tab>('a2a');

  const countByType = (t: string) => events.filter(e => e.type === t).length;
  const tabs: Array<{ id: Tab; label: string; color: string }> = [
    { id: 'a2a',      label: '😺 A2A',      color: 'var(--cyan)' },
    { id: 'mcp',      label: '🐾 MCP',      color: 'var(--green)' },
    { id: 'ucp',      label: '🛒 UCP',      color: 'var(--violet-bright)' },
    { id: 'acp',      label: '📋 ACP',      color: 'var(--pink)' },
    { id: 'payment',  label: '💳 Payment',  color: 'var(--gold)' },
    { id: 'timeline', label: '⚡ Timeline', color: '#FF9A3C' },
  ];

  const checkoutState = checkout?.state || ('none' as any);
  const ucpEvents     = events.filter(e => e.type === 'ucp');
  const ap2Token      = checkout?.ap2_token;

  return (
    <div className="inspector-panel">
      <div className="inspector-tabs">
        {tabs.map(t => (
          <div key={t.id} className={`inspector-tab${tab === t.id ? ' active' : ''}`}
            style={tab === t.id ? { color: t.color, borderBottomColor: t.color } : {}}
            onClick={() => setTab(t.id)}>
            {t.label}
            {countByType(t.id) > 0 && <span className="tab-badge">{countByType(t.id)}</span>}
          </div>
        ))}
      </div>

      <div className="inspector-content">
        {/* MCP Tab */}
        {tab === 'mcp' && (
          <>
            <div style={{ marginBottom: 16 }}>
              <h3 style={{ color: 'var(--green)', fontSize: '0.85rem', marginBottom: 6 }}>🔧 Model Context Protocol</h3>
              <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                Tool calls fired by the GPT-4o agent to the MCP server at <code style={{ color: 'var(--green)', fontSize: '0.65rem' }}>localhost:8001</code>. Each tool call is a structured function invocation with typed input/output.
              </p>
            </div>
            {events.filter(e => e.type === 'mcp').length === 0 ? (
              <div className="inspector-empty">
                <div className="icon">🔧</div>
                <p>MCP tool calls will appear here as the agent searches products, checks inventory, and applies discounts.</p>
              </div>
            ) : (
              events.filter(e => e.type === 'mcp').map(ev => <EventCard key={ev.id} ev={ev} />)
            )}
          </>
        )}

        {/* A2A Tab */}
        {tab === 'a2a' && (
          <>
            <div style={{ marginBottom: 16 }}>
              <h3 style={{ color: 'var(--cyan)', fontSize: '0.85rem', marginBottom: 6 }}>🤝 Agent2Agent Protocol</h3>
              <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                JSON-RPC 2.0 messages between the chat client (A2A client) and the merchant agent (A2A server). Agent discovered via <code style={{ color: 'var(--cyan)', fontSize: '0.65rem' }}>/.well-known/agent-card.json</code>.
              </p>
            </div>
            {agentInfo && (
              <div className="event-card" style={{ marginBottom: 12 }}>
                <div className="event-header">
                  <span className="event-tag tag-a2a">A2A</span>
                  <span className="event-title">Agent Card — /.well-known/agent-card.json</span>
                </div>
                <div className="event-body">
                  <div className="intent-label">💡 Agent discovery: the client fetches this on startup to understand what the agent can do and what extensions (UCP) it supports.</div>
                  <ColorJson data={agentInfo} />
                </div>
              </div>
            )}
            {events.filter(e => e.type === 'a2a').length === 0 ? (
              <div className="inspector-empty">
                <div className="icon">🤝</div>
                <p>A2A task messages (message/send, task state transitions) will appear here as you chat.</p>
              </div>
            ) : (
              events.filter(e => e.type === 'a2a').map(ev => <EventCard key={ev.id} ev={ev} />)
            )}
          </>
        )}

        {/* UCP Tab */}
        {tab === 'ucp' && (
          <>
            <div style={{ marginBottom: 16 }}>
              <h3 style={{ color: 'var(--violet-bright)', fontSize: '0.85rem', marginBottom: 6 }}>🛒 Universal Commerce Protocol</h3>
              <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                The UCP checkout lifecycle. Checkout state machine + capability negotiation via <code style={{ color: 'var(--violet-bright)', fontSize: '0.65rem' }}>/.well-known/ucp</code>.
              </p>
            </div>
            {checkout && (
              <>
                <div className="state-machine">
            {(['NOT_READY_FOR_PAYMENT', 'READY_FOR_PAYMENT', 'COMPLETED'] as const).map((s, i) => (
                      <React.Fragment key={s}>
                        {i > 0 && <span className="state-arrow">→</span>}
                        <div className={`state-node ${checkoutState === s ? 'active' : (
                          (['NOT_READY_FOR_PAYMENT', 'READY_FOR_PAYMENT', 'COMPLETED'].indexOf(checkoutState) > i) ? 'done' : ''
                      )}`}>{s}</div>
                    </React.Fragment>
                  ))}
                </div>
                <div className="event-card" style={{ marginBottom: 12 }}>
                  <div className="event-header">
                    <span className="event-tag tag-ucp">UCP</span>
                    <span className="event-title">Live Checkout Object</span>
                    <span className="event-ts">{ts()}</span>
                  </div>
                  <div className="event-body">
                    <div className="intent-label">💡 The checkout object evolves from NOT_READY_FOR_PAYMENT → READY_FOR_PAYMENT → COMPLETED as the agent collects shipping, payment, and confirmation.</div>
                    <ColorJson data={checkout} />
                  </div>
                </div>
              </>
            )}
            {ucpEvents.length === 0 && !checkout ? (
              <div className="inspector-empty">
                <div className="icon">🛒</div>
                <p>UCP checkout lifecycle events will appear here when the agent creates a checkout. Try asking to buy something!</p>
              </div>
            ) : (
              ucpEvents.map(ev => <EventCard key={ev.id} ev={ev} />)
            )}
          </>
        )}

        {/* ACP Tab */}
        {tab === 'acp' && (() => {
          const acpEvents = events.filter(e => e.type === 'acp');
          const acpState = checkout?.state === 'COMPLETED' ? 'COMPLETED'
            : checkout?.state === 'READY_FOR_PAYMENT' ? 'READY_FOR_PAYMENT'
            : checkout ? 'NOT_READY_FOR_PAYMENT' : null;
          const acpStates = ['NOT_READY_FOR_PAYMENT', 'READY_FOR_PAYMENT', 'COMPLETED'] as const;
          if (acpEvents.length > 0) {
            return (
              <>
                <div style={{ marginBottom: 12, padding: '10px 14px', background: 'rgba(255,110,199,0.08)', border: '1px solid var(--pink)', borderRadius: 10 }}>
                  <p style={{ fontSize: '0.72rem', color: 'var(--pink)', fontWeight: 600, marginBottom: 4 }}>📋 Live ACP Checkout (spec/2026-04-17)</p>
                  <p style={{ fontSize: '0.68rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                    These are real ACP checkout operations from this conversation, transported via MCP. Each call follows the <strong style={{color:'var(--pink)'}}>Agentic Commerce Protocol</strong> spec by OpenAI &amp; Stripe.
                  </p>
                </div>
                {acpState && (
                  <div className="state-machine" style={{ marginBottom: 16 }}>
                    {acpStates.map((s, i) => (
                      <React.Fragment key={s}>
                        {i > 0 && <span className="state-arrow">→</span>}
                        <div className={`state-node ${
                          acpState === s ? 'active' :
                          acpStates.indexOf(acpState) > i ? 'done' : ''
                        }`} style={{ borderColor: 'var(--pink)', ...(acpState === s ? { color: 'var(--pink)', background: 'rgba(255,110,199,0.06)' } : {}) }}>{s}</div>
                      </React.Fragment>
                    ))}
                  </div>
                )}
                {acpEvents.map(ev => <EventCard key={ev.id} ev={ev} />)}
              </>
            );
          }
          return (
            <>
              <div style={{ marginBottom: 16, padding: '10px 14px', background: 'rgba(255,110,199,0.08)', border: '1px solid var(--pink)', borderRadius: 10 }}>
                <p style={{ fontSize: '0.72rem', color: 'var(--pink)', fontWeight: 600, marginBottom: 4 }}>📋 ACP Integration Flow</p>
                <p style={{ fontSize: '0.68rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                  ACP (Agentic Commerce Protocol) by OpenAI &amp; Stripe. Start a checkout to see live ACP events here. Below is the documented integration path for context.
                </p>
              </div>
              {ACP_SIMULATION.map(ev => <EventCard key={ev.id} ev={ev} />)}
            </>
          );
        })()}

        {/* Timeline Tab */}
        {tab === 'timeline' && (
          <>
            <div style={{ marginBottom: 16 }}>
              <h3 style={{ color: '#FF9A3C', fontSize: '0.85rem', marginBottom: 6 }}>⚡ Real-Time Protocol Trace</h3>
              <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                Live WebSocket stream from <code style={{ color: '#FF9A3C', fontSize: '0.65rem' }}>ws://localhost:10999/ws/trace</code>.
                Every MCP tool call, A2A task event, and UCP checkout transition is pushed here in real time with per-step latency.
                Latency badges: <span style={{ color: 'var(--green)' }}>&lt;200ms</span> · <span style={{ color: 'var(--gold)' }}>&lt;800ms</span> · <span style={{ color: 'var(--pink)' }}>&gt;800ms</span>
              </p>
            </div>
            {traceEvents.length === 0 ? (
              <div className="inspector-empty">
                <div className="icon">⚡</div>
                <p>Waiting for trace events. Send a message to the agent — each tool call and state transition will stream here with latency data.</p>
              </div>
            ) : (
              <div>
                {[...traceEvents].reverse().map((ev, i) => (
                  <TimelineItem key={i} ev={ev} />
                ))}
              </div>
            )}
          </>
        )}

        {/* Payment Tab */}
        {tab === 'payment' && (
          <>
            <div style={{ marginBottom: 16 }}>
              <h3 style={{ color: 'var(--gold)', fontSize: '0.85rem', marginBottom: 6 }}>💳 AP2 — Agentic Payment Token</h3>
              <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                When the agent completes checkout, an AP2 agentic payment token is issued. It's a scoped, time-limited, single-use, cryptographically-signed mandate that authorizes the agent to spend on the user's behalf.
              </p>
            </div>
            {ap2Token ? (
              <>
                <TokenVisualizer token={ap2Token} />
                {events.filter(e => e.type === 'payment').map(ev => <EventCard key={ev.id} ev={ev} />)}
              </>
            ) : (
              <div className="inspector-empty">
                <div className="icon">💳</div>
                <p>The AP2 agentic payment token will appear here after you complete checkout. It shows the scoped authorization an agent carries to spend on your behalf.</p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ── Main App ───────────────────────────────────────────────────────────────────
export default function App() {
  const [messages, setMessages]       = useState<Message[]>([
    { id: 'welcome', role: 'agent', text: "Hi! I'm Max, the My Store shopping assistant.\n\nAsk me about our products — apparel, drinkware, and accessories. I can search the catalog, check stock, apply discount codes, and walk you through checkout.\n\nThe right panel shows the protocol layer live: MCP tool calls, A2A messages, UCP checkout lifecycle, ACP checkout sessions, and the payment token when you complete an order." }
  ]);
  const [input, setInput]             = useState('');
  const [loading, setLoading]         = useState(false);
  const [contextId]                   = useState(() => uid());
  const [events, setEvents]           = useState<ProtocolEvent[]>([]);
  const [checkout, setCheckout]       = useState<CheckoutData | null>(null);
  const [agentInfo, setAgentInfo]     = useState<object | null>(null);
  const [agentOnline, setAgentOnline] = useState(false);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const bottomRef                     = useRef<HTMLDivElement>(null);
  const wsRef                         = useRef<WebSocket | null>(null);

  // WebSocket trace stream
  useEffect(() => {
    const connect = () => {
      try {
        const ws = new WebSocket(WS_URL);
        wsRef.current = ws;
        ws.onmessage = (e) => {
          try {
            const ev: TraceEvent = JSON.parse(e.data);
            setTraceEvents(prev => [...prev.slice(-199), ev]);
          } catch {}
        };
        ws.onclose = () => {
          // Reconnect after 3s if agent is online
          setTimeout(connect, 3000);
        };
      } catch {}
    };
    connect();
    return () => wsRef.current?.close();
  }, []);

  // Fetch agent card on mount
  useEffect(() => {
    (async () => {
      try {
        const r = await axios.get(`${AGENT_URL}/.well-known/agent-card.json`);
        setAgentInfo(r.data);
        setAgentOnline(true);
        addEvent({ type: 'rest', title: 'GET /.well-known/agent-card.json', intent: 'Client discovers the agent capabilities, A2A version, UCP extensions, and security schemes on startup.', data: r.data });
      } catch {
        setAgentOnline(false);
      }
    })();
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, loading]);

  const addEvent = useCallback((ev: Omit<ProtocolEvent, 'id' | 'timestamp'>) => {
    setEvents(prev => [...prev, { ...ev, id: uid(), timestamp: ts() }]);
  }, []);

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || loading) return;
    const userMsg: Message = { id: uid(), role: 'user', text };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setLoading(true);

    // A2A: record outbound message
    const a2aRequest = { jsonrpc: '2.0', id: uid(), method: 'message/send', params: { contextId, message: { parts: [{ kind: 'text', text }] } } };
    addEvent({ type: 'a2a', title: 'A2A: message/send →', intent: 'Client sends user message to the A2A server (merchant agent) as a JSON-RPC 2.0 request.', data: a2aRequest });

    // REST: record the HTTP call
    addEvent({ type: 'rest', title: 'POST /a2a', intent: 'HTTP POST to merchant agent A2A endpoint. The request body is a JSON-RPC 2.0 envelope.', data: { method: 'POST', url: `${AGENT_URL}/a2a`, headers: { 'Content-Type': 'application/json' }, body: a2aRequest } });

    try {
      const r = await axios.post(`${AGENT_URL}/a2a`, a2aRequest);
      const result = r.data?.result;

      // A2A response
      addEvent({ type: 'a2a', title: 'A2A: task completed ←', intent: 'Merchant agent returns completed task with artifacts (agent response text) and metadata (tool events, checkout).', data: result || r.data });

      // MCP tool events — ACP checkout tools go to the 'acp' tab
      const ACP_CHECKOUT_TOOLS = new Set([
        'create_checkout_session', 'update_checkout_session', 'get_checkout_session',
        'complete_checkout_session', 'cancel_checkout_session',
      ]);
      const ACP_TITLES: Record<string, string> = {
        create_checkout_session:   'ACP: POST /checkout_sessions',
        update_checkout_session:   'ACP: POST /checkout_sessions/{id}',
        get_checkout_session:      'ACP: GET /checkout_sessions/{id}',
        complete_checkout_session: 'ACP: POST /checkout_sessions/{id}/complete',
        cancel_checkout_session:   'ACP: POST /checkout_sessions/{id}/cancel',
      };
      const toolEvents: any[] = result?.metadata?.tool_events || [];
      for (const ev of toolEvents) {
        const isAcp  = ACP_CHECKOUT_TOOLS.has(ev.tool);
        const evType = isAcp ? 'acp' : 'mcp';
        const title  = isAcp ? (ACP_TITLES[ev.tool] || `ACP: ${ev.tool}`) : `MCP: ${ev.tool}`;
        addEvent({ type: evType, title, intent: getMcpIntent(ev.tool), data: { tool: ev.tool, input: ev.input, output: ev.output } });
      }

      // UCP checkout
      const ucpCheckout = result?.metadata?.ucp_checkout;
      if (ucpCheckout) {
        setCheckout(ucpCheckout);
        addEvent({ type: 'ucp', title: `UCP: POST /ucp/checkout/sessions`, intent: 'Agent created a UCP checkout object. Checkout state = NOT_READY_FOR_PAYMENT. Waiting for shipping address and payment instrument.', data: ucpCheckout });
      }

      // Extract agent text
      const agentText = result?.artifacts?.[0]?.parts?.find((p: any) => p.kind === 'text')?.text || r.data?.result?.artifacts?.[0]?.parts?.[0]?.text || 'Done!';
      const agentMsg: Message = { id: uid(), role: 'agent', text: agentText, checkout: ucpCheckout };
      setMessages(prev => [...prev, agentMsg]);

    } catch (err: any) {
      setMessages(prev => [...prev, { id: uid(), role: 'agent', text: `❌ Error: ${err.message}. Is the agent running? Check docker-compose up.` }]);
    }
    setLoading(false);
  }, [loading, contextId, addEvent]);

  const handleCheckoutUpdate = useCallback((updated: CheckoutData) => {
    setCheckout(updated);
    // Record UCP state transition with REST-style title
    const ucpTitle = updated.state === 'READY_FOR_PAYMENT'
      ? `UCP: POST /ucp/checkout/${updated.id}/complete`
      : updated.state === 'COMPLETED'
      ? `UCP: POST /ucp/checkout/${updated.id}/confirm`
      : `UCP: checkout → ${updated.state}`;
    addEvent({ type: 'ucp', title: ucpTitle, intent: `Checkout transitioned to ${updated.state}. ${updated.state === 'COMPLETED' ? 'AP2 agentic payment token issued.' : 'Waiting for payment confirmation.'}`, data: updated });

    // Mirror to ACP tab — fire the equivalent ACP call for each state transition
    if (updated.state === 'READY_FOR_PAYMENT') {
      addEvent({
        type: 'acp',
        title: `ACP: PATCH /checkout_sessions/${updated.id}`,
        intent: 'Checkout session updated with shipping address and payment instrument selection. ACP session transitions to ready_for_payment.',
        data: {
          acp_operation: 'update_checkout_session',
          session_id: updated.id,
          new_state: 'READY_FOR_PAYMENT',
          fulfillment_option_selected: 'standard',
          payment_handler: 'agentic_payment_v2',
          subtotal: updated.subtotal,
          shipping: updated.shipping,
          total: updated.total,
        },
      });
    }
    if (updated.state === 'COMPLETED') {
      addEvent({
        type: 'acp',
        title: `ACP: POST /checkout_sessions/${updated.id}/complete`,
        intent: 'Checkout session completed. Merchant confirms the order and issues an AP2 agentic payment token for authorization.',
        data: {
          acp_operation: 'complete_checkout_session',
          session_id: updated.id,
          new_state: 'COMPLETED',
          order_id: updated.order_id,
          ap2_token_id: updated.ap2_token?.token_id,
          total: updated.total,
        },
      });
    }

    // If completed, record AP2 token event
    if (updated.ap2_token) {
      addEvent({ type: 'payment', title: 'AP2: Payment Token Issued', intent: 'An agentic payment token was issued. It cryptographically authorizes the agent to complete this specific purchase at this specific merchant up to the specified max_amount.', data: updated.ap2_token });
    }
    // Update checkout in last agent message
    setMessages(prev => prev.map(m => m.checkout?.id === updated.id ? { ...m, checkout: updated } : m));
  }, [addEvent]);

  const quickPrompts = [
    'What are your bestsellers?',
    'Show me what you have in stock',
    'Tell me about the Enamel Mug',
    'What do you recommend?',
    "What's your return policy?",
  ];

  return (
    <div className="app">
      <div className="header">
        <div className="header-logo">�️ <span>My Store</span></div>

        <div className="header-right">
          <div className={`status-dot${agentOnline ? '' : ' offline'}`} />
          <span>{agentOnline ? 'Agent online' : 'Agent offline'}</span>
          <span style={{ color: 'var(--border)' }}>|</span>
          <span>Agentic Commerce Starter Kit</span>
        </div>
      </div>

      <div className="main">
        {/* Chat */}
        <div className="chat-panel">
          <div className="chat-messages">
            {messages.map(msg => (
              <div key={msg.id} className={`message ${msg.role}`}>
                <div className="message-role">{msg.role === 'user' ? '👤 You' : '🤖 Max (Store Agent)'}</div>
                <div className="message-bubble">{msg.text}</div>
                {msg.checkout && msg.checkout.state !== 'COMPLETED' && (
                  <CheckoutCard checkout={msg.checkout} onUpdate={handleCheckoutUpdate} />
                )}
                {msg.checkout && msg.checkout.state === 'COMPLETED' && (
                  <div className="checkout-card" style={{ marginTop: 8 }}>
                    <div className="order-confirmed">✅ Order {msg.checkout.order_id} confirmed!</div>
                    {msg.checkout.ap2_token && (
                      <div style={{ marginTop: 8, fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                        🔐 AP2 token <code style={{ color: 'var(--gold)' }}>{msg.checkout.ap2_token.token_id}</code> issued. See Payment tab →
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
            {loading && (
              <div className="message agent">
                <div className="message-role">🤖 Max (Store Agent)</div>
                <div className="typing-indicator">
                  <div className="typing-dot" /><div className="typing-dot" /><div className="typing-dot" />
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="chat-input-area">
            <div className="quick-prompts">
              {quickPrompts.map(p => (
                <button key={p} className="quick-btn" onClick={() => sendMessage(p)}>{p}</button>
              ))}
            </div>
            <div className="chat-input-row">
              <textarea
                className="chat-input"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(input); } }}
                placeholder="Ask the agent anything… (Enter to send)"
                rows={1}
              />
              <button className="send-btn" disabled={!input.trim() || loading} onClick={() => sendMessage(input)}>→</button>
            </div>
          </div>
        </div>

        {/* Inspector */}
        <InspectorPanel events={events} checkout={checkout} agentInfo={agentInfo} traceEvents={traceEvents} />
      </div>
    </div>
  );
}

function getMcpIntent(tool: string): string {
  const intents: Record<string, string> = {
    product_search:            'Agent calls the MCP product_search tool to find items matching the user\'s query. Any agent speaking MCP can use this tool — that\'s the power of the protocol.',
    inventory_check:           'Agent verifies stock levels for a specific product before adding to cart. Prevents checkout failures due to out-of-stock items.',
    apply_discount:            'Agent applies a discount code to the cart subtotal. The MCP tool handles business logic; the agent just calls it.',
    get_product_details:       'Agent fetches full product details including allergens, weight, shelf life, and ingredients note for a specific item.',
    get_recommendations:       'Agent retrieves personalized product recommendations, optionally based on a product or category.',
    get_store_policy:          'Agent fetches store policies: return policy, allergen warnings, shipping options, and operating hours.',
    get_bestsellers:           'Agent retrieves the top 3 bestselling in-stock products ranked by popularity.',
    create_checkout_session:   'Agent opens an ACP checkout session (spec/2026-04-17) with line items, fulfillment options, and AP2 payment handler capabilities.',
    update_checkout_session:   'Agent updates the ACP checkout session — typically to select a fulfillment/shipping option, which recalculates totals.',
    get_checkout_session:      'Agent reads the current state of an ACP checkout session to verify status or retrieve updated totals.',
    complete_checkout_session: 'Agent completes the ACP checkout — the merchant confirms the order and issues an AP2 agentic payment token.',
    cancel_checkout_session:   'Agent cancels the ACP checkout session and marks it as canceled in the session store.',
  };
  return intents[tool] || `MCP tool call: ${tool}`;
}
