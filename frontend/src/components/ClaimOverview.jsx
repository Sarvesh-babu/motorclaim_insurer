function premiumPaidTillIncident(policy, incidentDateStr) {
  try {
    const start    = new Date(policy.policy_start)
    const end      = new Date(policy.policy_end)
    const incident = new Date(incidentDateStr)
    const premium  = Number(policy.annual_premium)
    if (!premium) return null
    // Cap elapsed days at the policy period; floor at 0
    const policyDays  = Math.max((end - start) / 86400000, 1)
    const elapsedDays = Math.min(Math.max((incident - start) / 86400000, 0), policyDays)
    // annual_premium is per year (365 days), regardless of how long the policy runs
    return Math.round(premium * elapsedDays / 365)
  } catch {
    return null
  }
}

export default function ClaimOverview({ claim, policy }) {
  if (!claim) return null

  const claimFields = [
    { label: 'Policy No.',     value: claim.policy_no },
    { label: 'Type',           value: claim.claim_type },
    { label: 'Incident Date',  value: claim.incident_date },
    { label: 'Location',       value: claim.incident_location },
    { label: 'Submitted',      value: claim.created_at },
  ]

  const paidTillIncident = policy
    ? premiumPaidTillIncident(policy, claim.incident_date)
    : null

  return (
    <div className="bg-slate-900 rounded-2xl border border-slate-700 p-5">
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div>
          <p className="text-xs font-mono text-indigo-400 mb-1">{claim.claim_id}</p>
          <h2 className="text-xl font-bold text-white">{claim.claimant}</h2>
        </div>
        <div className="text-right">
          {/* garage_estimate_amount = what the customer actually claimed */}
          {Number(claim.garage_estimate_amount) > 0 ? (
            <div className="space-y-0.5">
              <p className="text-2xl font-bold text-white">
                ₹{Number(claim.garage_estimate_amount).toLocaleString('en-IN')}
              </p>
              <p className="text-xs text-slate-400">Customer Claimed</p>
              {Number(claim.claim_amount) > 0 && (
                <p className="text-xs text-indigo-300">
                  AI Assessed: ₹{Number(claim.claim_amount).toLocaleString('en-IN')}
                </p>
              )}
            </div>
          ) : (
            <div>
              <p className="text-2xl font-bold text-white">
                ₹{Number(claim.claim_amount).toLocaleString('en-IN')}
              </p>
              <p className="text-xs text-slate-400">AI Assessed</p>
            </div>
          )}
        </div>
      </div>

      <p className="text-sm text-slate-300 mb-4 leading-relaxed">{claim.description}</p>

      {/* Claim fields */}
      <div className="grid grid-cols-2 gap-2">
        {claimFields.map(f => (
          <div key={f.label} className="bg-slate-800 rounded-lg px-3 py-2">
            <p className="text-xs text-slate-500 mb-0.5">{f.label}</p>
            <p className="text-sm text-slate-200 truncate">{f.value}</p>
          </div>
        ))}
      </div>

      {/* Policy coverage section */}
      {policy && (
        <div className="mt-4 pt-4 border-t border-slate-700">
          <p className="text-xs text-slate-500 uppercase tracking-wider font-medium mb-2">Policy Coverage</p>
          <div className="grid grid-cols-2 gap-2">
            <div className="bg-slate-800 rounded-lg px-3 py-2">
              <p className="text-xs text-slate-500 mb-0.5">Policy Start</p>
              <p className="text-sm text-slate-200">{policy.policy_start}</p>
            </div>
            <div className="bg-slate-800 rounded-lg px-3 py-2">
              <p className="text-xs text-slate-500 mb-0.5">Policy End</p>
              <p className="text-sm text-slate-200">{policy.policy_end}</p>
            </div>
            <div className="bg-slate-800 rounded-lg px-3 py-2">
              <p className="text-xs text-slate-500 mb-0.5">Annual Premium</p>
              <p className="text-sm text-slate-200">₹{Number(policy.annual_premium).toLocaleString('en-IN')}</p>
            </div>
            <div className="bg-indigo-950/50 border border-indigo-500/25 rounded-lg px-3 py-2">
              <p className="text-xs text-indigo-400 mb-0.5">Premium Paid till Incident</p>
              <p className="text-sm font-semibold text-indigo-300">
                {paidTillIncident !== null
                  ? `₹${paidTillIncident.toLocaleString('en-IN')}`
                  : '—'}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
