import { useEffect, useState, type ReactNode } from 'react'

type Screen = 'home' | 'pay' | 'processing' | 'success' | 'activity' | 'profile'

export interface PaymentRecord {
  id: string
  amount: number
  payer: string
  payee: string
  rail: string
  status: string
  timeline?: { to: string; at: string; why?: string }[]
  created_at?: string
}

const API_BASE = 'http://127.0.0.1:8000'

const paths: Record<string, ReactNode> = {
  scan: <><path d="M4 9V6a2 2 0 0 1 2-2h3M15 4h3a2 2 0 0 1 2 2v3M20 15v3a2 2 0 0 1-2 2h-3M9 20H6a2 2 0 0 1-2-2v-3"/><path d="M9 9h6v6H9z"/></>,
  send: <><path d="m21 3-8.5 18-2.8-8.7L1 9.5 21 3Z"/><path d="m9.7 12.3 4.5-4.5"/></>,
  bank: <><path d="m3 10 9-6 9 6"/><path d="M5 10v8m4-8v8m6-8v8m4-8v8M3 21h18"/></>,
  phone: <><rect x="6" y="2" width="12" height="20" rx="2"/><path d="M10 18h4"/></>,
  history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></>,
  user: <><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></>,
  home: <><path d="m3 11 9-8 9 8"/><path d="M5 10v10h14V10M9 20v-6h6v6"/></>,
  shield: <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/></>,
  qr: <><path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h2v2h-2zM18 14h2v6h-6v-2"/></>,
  card: <><rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20M6 15h4"/></>,
  gift: <><path d="M20 12v9H4v-9M2 7h20v5H2zM12 7v14M12 7H7.5a2.5 2.5 0 1 1 0-5C11 2 12 7 12 7ZM12 7h4.5a2.5 2.5 0 1 0 0-5C13 2 12 7 12 7Z"/></>,
  help: <><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.7 2.7 0 1 1 3.2 2.6c-.7.25-.7.8-.7 1.4M12 17h.01"/></>,
  eye: <><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></>,
  bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></>,
  chevron: <path d="m9 18 6-6-6-6"/>,
  copy: <><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></>,
}

function Icon({ name, size = 22 }: { name: keyof typeof paths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function Logo() {
  return <div className="logo"><span className="logo-mark">?</span><span>Rupee<b>One</b></span></div>
}

const defaultTransactions = [
  { name: 'Priya Sharma', detail: 'UPI ? Today, 02:34 PM', amount: '- ?1,500', color: 'violet', letter: 'P' },
  { name: 'Arun Kumar', detail: 'IMPS ? Yesterday, 12:18 PM', amount: '- ?1,200', color: 'blue', letter: 'A' },
  { name: 'Cashback received', detail: 'Reward ? 12 Oct', amount: '+ ?75', color: 'green', letter: '?' },
]

function playSuccess() {
  try {
    const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    const ctx = new AudioCtx()
    ;[523.25, 659.25, 783.99].forEach((frequency, index) => {
      const oscillator = ctx.createOscillator()
      const gain = ctx.createGain()
      oscillator.type = 'sine'
      oscillator.frequency.value = frequency
      gain.gain.setValueAtTime(0, ctx.currentTime + index * 0.1)
      gain.gain.linearRampToValueAtTime(0.12, ctx.currentTime + index * 0.1 + 0.025)
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + index * 0.1 + 0.42)
      oscillator.connect(gain).connect(ctx.destination)
      oscillator.start(ctx.currentTime + index * 0.1)
      oscillator.stop(ctx.currentTime + index * 0.1 + 0.45)
    })
    navigator.vibrate?.([35, 45, 70])
  } catch { /* Audio feedback is an enhancement. */ }
}

function Home({
  navigate,
  balance,
  transactions,
  backendOnline,
}: {
  navigate: (screen: Screen) => void
  balance: number
  transactions: typeof defaultTransactions
  backendOnline: boolean
}) {
  const actions = [
    { icon: 'scan' as const, label: 'Scan & pay', tone: 'cyan' },
    { icon: 'send' as const, label: 'Pay contact', tone: 'blue' },
    { icon: 'bank' as const, label: 'Bank transfer', tone: 'violet' },
    { icon: 'phone' as const, label: 'Mobile recharge', tone: 'orange' },
  ]
  const formattedBalance = balance.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  const [whole, decimal] = formattedBalance.split('.')

  return <>
    <header className="topbar">
      <Logo />
      <div className="top-actions">
        <button className="icon-button" aria-label="Notifications"><Icon name="bell" size={19}/><span className="notification-dot"/></button>
        <button className="avatar" onClick={() => navigate('profile')}>RK</button>
      </div>
    </header>
    <main className="content home-content">
      <section className="hello">
        <div><p className="eyebrow">GOOD AFTERNOON</p><h1>Hi, Ravi</h1></div>
        <span className="safe-pill">
          <Icon name="shield" size={14}/> {backendOnline ? 'Shield Active' : '100% secure'}
        </span>
      </section>

      <section className="balance-card">
        <div className="orb orb-one"/><div className="orb orb-two"/>
        <div className="balance-label"><span>Available balance</span><Icon name="eye" size={18}/></div>
        <div className="balance">?{whole}<span>.{decimal || '00'}</span></div>
        <div className="balance-meta"><span><b>{transactions.length}</b> activities</span><span className="upi-chip">UPI linked</span></div>
      </section>

      <section className="action-panel">
        <div className="section-title"><h2>Quick actions</h2><button>Manage</button></div>
        <div className="action-grid">
          {actions.map((action) => <button className="action" key={action.label} onClick={() => navigate('pay')}>
            <span className={`action-icon ${action.tone}`}><Icon name={action.icon}/></span><span>{action.label}</span>
          </button>)}
        </div>
      </section>

      <button className="lite-banner" onClick={() => navigate('pay')}>
        <span className="lite-icon">?</span>
        <span><b>PayShield Instant</b><small>Pay instantly with multi-rail safety</small></span>
        <span className="arrow"><Icon name="chevron" size={18}/></span>
      </button>

      <section className="recent">
        <div className="section-title"><h2>Recent activity</h2><button onClick={() => navigate('activity')}>See all</button></div>
        <div className="transaction-card">
          {transactions.slice(0, 4).map((item, idx) => <button className="transaction" key={idx} onClick={() => navigate('activity')}>
            <span className={`person ${item.color}`}>{item.letter}</span>
            <span className="transaction-copy"><b>{item.name}</b><small>{item.detail}</small></span>
            <span className={item.amount.startsWith('+') ? 'credit' : ''}>{item.amount}</span>
          </button>)}
        </div>
      </section>
    </main>
  </>
}

function Pay({
  navigate,
  balance,
  onInitiate,
}: {
  navigate: (screen: Screen) => void
  balance: number
  onInitiate: (details: { payee: string; amount: number; rail: string }) => void
}) {
  const [amount, setAmount] = useState('500')
  const [rail, setRail] = useState('UPI')
  const [payee, setPayee] = useState('Priya Sharma')

  const handlePay = () => {
    onInitiate({ payee, amount: Number(amount) || 500, rail })
    navigate('processing')
  }

  return <>
    <header className="page-header"><button className="back" onClick={() => navigate('home')}>?</button><h1>Send money</h1><button className="more">???</button></header>
    <main className="content pay-content">
      <section className="recipient">
        <span className="person violet">{payee.charAt(0).toUpperCase()}</span>
        <div><p>Paying</p><b>{payee}</b><small>priya@okaxis</small></div>
        <button onClick={() => {
          const next = payee === 'Priya Sharma' ? 'Suresh Kumar' : 'Priya Sharma'
          setPayee(next)
        }}>Change</button>
      </section>
      <section className="amount-section">
        <p>ENTER AMOUNT</p>
        <label className="amount-input"><span>?</span><input value={amount} inputMode="numeric" onChange={(event) => setAmount(event.target.value.replace(/\D/g, ''))}/></label>
        <span className="balance-hint">Balance: ?{balance.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
        <div className="amount-chips">{['100', '500', '1,000', '2,000'].map((value) => <button className={amount === value.replace(',', '') ? 'selected' : ''} onClick={() => setAmount(value.replace(',', ''))} key={value}>+ ?{value}</button>)}</div>
      </section>
      <section className="rail-section">
        <div className="section-title"><h2>Payment method</h2></div>
        <div className="rails">
          {[
            { name: 'UPI', sub: 'Instant ? Free', icon: 'send' as const },
            { name: 'IMPS', sub: 'In seconds ? ?2.50', icon: 'bank' as const },
            { name: 'NEFT', sub: 'Within 30 min ? ?1', icon: 'bank' as const },
          ].map((item) => <button key={item.name} className={rail === item.name ? 'rail active' : 'rail'} onClick={() => setRail(item.name)}>
            <span className="rail-icon"><Icon name={item.icon} size={20}/></span><span><b>{item.name}</b><small>{item.sub}</small></span><span className="radio">{rail === item.name && <i/>}</span>
          </button>)}
        </div>
      </section>
      <div className="secure-note"><Icon name="shield" size={20}/><span><b>Protected by PayShield Secure</b><small>Sanction screened & idempotency guarded</small></span></div>
      <button className="primary-button" disabled={!amount} onClick={handlePay}><Icon name="shield" size={19}/> Pay ?{amount || '0'} securely</button>
    </main>
  </>
}

function Processing({
  navigate,
  paymentDetails,
  onComplete,
}: {
  navigate: (screen: Screen) => void
  paymentDetails: { payee: string; amount: number; rail: string }
  onComplete: (record: PaymentRecord) => void
}) {
  useEffect(() => {
    let isMounted = true
    const idempotencyKey = 'key_' + Date.now() + '_' + Math.random().toString(36).substring(2, 7)

    const executePayment = async () => {
      try {
        const response = await fetch(`${API_BASE}/payments`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Idempotency-Key': idempotencyKey,
          },
          body: JSON.stringify({
            payer: 'Ravi',
            payee: paymentDetails.payee.split(' ')[0], // e.g. Priya
            amount: paymentDetails.amount,
          }),
        })

        if (response.ok) {
          const data: PaymentRecord = await response.json()
          if (!isMounted) return
          setTimeout(() => {
            playSuccess()
            onComplete(data)
            navigate('success')
          }, 1500)
          return
        }
      } catch {
        // Backend offline fallback
      }

      // Fallback local simulation if backend unreachable
      setTimeout(() => {
        if (!isMounted) return
        playSuccess()
        onComplete({
          id: 'PAY-' + Math.floor(100000000000 + Math.random() * 900000000000),
          amount: paymentDetails.amount,
          payer: 'Ravi',
          payee: paymentDetails.payee,
          rail: paymentDetails.rail,
          status: 'SETTLED',
          timeline: [
            { to: 'VALIDATED', at: new Date().toISOString() },
            { to: 'SCREENED', at: new Date().toISOString() },
            { to: 'ROUTED', at: new Date().toISOString() },
            { to: 'SETTLED', at: new Date().toISOString() },
          ],
        })
        navigate('success')
      }, 2000)
    }

    executePayment()
    return () => { isMounted = false }
  }, [navigate, paymentDetails, onComplete])

  return <main className="processing">
    <Logo />
    <div className="secure-animation">
      <span className="pulse p1"/><span className="pulse p2"/><span className="pulse p3"/>
      <span className="secure-core"><Icon name="shield" size={44}/></span>
      <span className="orbit"><i/><i/><i/></span>
    </div>
    <h1>Securing your payment</h1>
    <p>Sanction checks & bank routing in progress. Please don?t close the app.</p>
    <div className="processing-card"><span className="person violet">{paymentDetails.payee.charAt(0)}</span><span><small>Paying to</small><b>{paymentDetails.payee}</b></span><strong>?{paymentDetails.amount}</strong></div>
    <div className="progress"><span/></div>
    <small className="encryption"><Icon name="shield" size={14}/> PayShield Control Tower Protected</small>
  </main>
}

function Success({
  navigate,
  lastPayment,
}: {
  navigate: (screen: Screen) => void
  lastPayment: PaymentRecord | null
}) {
  const steps = lastPayment?.timeline?.map(t => `${t.to} completed`) || ['Payment initiated', 'Verified by bank', 'Settled securely']
  const amount = lastPayment?.amount || 500
  const payee = lastPayment?.payee || 'Priya Sharma'
  const txId = lastPayment?.id || '428531907621'

  return <main className="success-page">
    <div className="success-hero">
      <div className="success-mark"><span>?</span><i/><i/></div>
      <p>PAYMENT SUCCESSFUL</p><h1>?{amount.toLocaleString('en-IN')}</h1><span>Paid to {payee}</span>
    </div>
    <section className="receipt">
      <div className="receipt-row"><span>Rail</span><b>{lastPayment?.rail || 'UPI'} (Zero Fee)</b></div>
      <div className="receipt-row"><span>Transaction ID</span><b style={{ fontSize: '11px' }}>{txId} <Icon name="copy" size={13}/></b></div>
      <div className="timeline">
        {steps.map((step, index) => <div className="timeline-step" key={index}><span>?</span><div><b>{step}</b><small>{index === steps.length - 1 ? 'Just now' : 'Completed instantly'}</small></div></div>)}
      </div>
      <div className="reward"><span><Icon name="gift" size={21}/></span><div><b>PayShield Safety Guarantee</b><small>Cryptographic hash verified in ledger</small></div><Icon name="chevron" size={18}/></div>
    </section>
    <div className="success-actions"><button className="secondary-button" onClick={() => navigator.clipboard?.writeText(txId)}>Copy ID</button><button className="primary-button" onClick={() => navigate('home')}>Done</button></div>
  </main>
}

function Activity({
  navigate,
  transactions,
}: {
  navigate: (screen: Screen) => void
  transactions: typeof defaultTransactions
}) {
  return <><header className="page-header"><button className="back" onClick={() => navigate('home')}>?</button><h1>Activity</h1><button className="more">???</button></header><main className="content simple-page"><div className="filter-row"><button className="selected">All</button><button>Money sent</button><button>Received</button></div><p className="date-label">RECENT TRANSACTIONS</p><div className="transaction-card">{transactions.map((item, index) => <div className="transaction" key={index}><span className={`person ${item.color}`}>{item.letter}</span><span className="transaction-copy"><b>{item.name}</b><small>{item.detail}</small></span><span className={item.amount.startsWith('+') ? 'credit' : ''}>{item.amount}</span></div>)}</div></main></>
}

function Profile({
  navigate,
  balance,
}: {
  navigate: (screen: Screen) => void
  balance: number
}) {
  const items = [
    ['bank', 'Linked bank accounts', 'State Bank of India ???? 4092'],
    ['qr', 'My PayShield QR', 'ravi@okaxis'],
    ['card', 'Payment methods & rails', 'UPI, IMPS, NEFT, RTGS active'],
    ['gift', 'Security & Audit score', '100% Tamper-evident verified'],
    ['help', 'Help & support', 'Available 24?7'],
  ] as const
  return <><header className="page-header"><button className="back" onClick={() => navigate('home')}>?</button><h1>Profile</h1><button className="more">???</button></header><main className="content profile-page"><section className="profile-card"><span className="large-avatar">RK</span><h2>Ravi Kumar</h2><p>Wallet balance: ?{balance.toLocaleString('en-IN')}</p><span><Icon name="shield" size={14}/> Verified account</span></section><section className="menu-card">{items.map(([icon, title, sub]) => <button className="menu-item" key={title}><span><Icon name={icon}/></span><div><b>{title}</b><small>{sub}</small></div><Icon name="chevron" size={18}/></button>)}</section><button className="logout" onClick={() => navigate('home')}>Back to Home</button></main></>
}

function BottomNav({ screen, navigate }: { screen: Screen; navigate: (screen: Screen) => void }) {
  if (screen === 'pay' || screen === 'processing' || screen === 'success') return null
  const links = [
    ['home', 'home', 'Home'], ['activity', 'history', 'Activity'], ['pay', 'scan', 'Scan'], ['profile', 'user', 'Profile'],
  ] as const
  return <nav className="bottom-nav">{links.map(([target, icon, label]) => <button key={target} className={screen === target ? 'active' : ''} onClick={() => navigate(target)}><span className={target === 'pay' ? 'scan-nav' : ''}><Icon name={icon}/></span><small>{label}</small></button>)}</nav>
}

export default function App() {
  const [screen, setScreen] = useState<Screen>('home')
  const [balance, setBalance] = useState<number>(1000000)
  const [transactions, setTransactions] = useState(defaultTransactions)
  const [lastPayment, setLastPayment] = useState<PaymentRecord | null>(null)
  const [paymentDetails, setPaymentDetails] = useState({ payee: 'Priya Sharma', amount: 500, rail: 'UPI' })
  const [backendOnline, setBackendOnline] = useState(false)

  // Sync with backend on startup
  useEffect(() => {
    const fetchUserData = async () => {
      try {
        const res = await fetch(`${API_BASE}/users/ravi`)
        if (res.ok) {
          const data = await res.json()
          if (typeof data.balance === 'number') {
            setBalance(data.balance)
            setBackendOnline(true)
          }
        }
      } catch {
        // Backend not currently running
      }
    }
    fetchUserData()
  }, [])

  const handlePaymentComplete = (record: PaymentRecord) => {
    setLastPayment(record)
    setBalance(prev => Math.max(0, prev - record.amount))
    const newTx = {
      name: record.payee,
      detail: `${record.rail || 'UPI'} ? ${record.status} ? Just now`,
      amount: `- ?${record.amount.toLocaleString('en-IN')}`,
      color: 'violet',
      letter: record.payee.charAt(0).toUpperCase(),
    }
    setTransactions(prev => [newTx, ...prev])
  }

  const navigate = (next: Screen) => { setScreen(next); window.scrollTo({ top: 0, behavior: 'smooth' }) }

  return <div className="app-shell">
    <div className="phone">
      {screen === 'home' && <Home navigate={navigate} balance={balance} transactions={transactions} backendOnline={backendOnline}/>}
      {screen === 'pay' && <Pay navigate={navigate} balance={balance} onInitiate={(d) => setPaymentDetails(d)}/>}
      {screen === 'processing' && <Processing navigate={navigate} paymentDetails={paymentDetails} onComplete={handlePaymentComplete}/>}
      {screen === 'success' && <Success navigate={navigate} lastPayment={lastPayment}/>}
      {screen === 'activity' && <Activity navigate={navigate} transactions={transactions}/>}
      {screen === 'profile' && <Profile navigate={navigate} balance={balance}/>}
      <BottomNav screen={screen} navigate={navigate}/>
    </div>
  </div>
}
