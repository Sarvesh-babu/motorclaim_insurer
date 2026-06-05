import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../utils/api'
import { useAuth } from '../auth/AuthContext'

function PolicyCard({ policy }) {
  return (
    <div className="bg-indigo-950/40 border border-indigo-500/40 rounded-xl p-4 mb-5">
      <div className="flex items-start justify-between mb-2">
        <div>
          <p className="text-xs text-indigo-400 font-mono">{policy.policy_no}</p>
          <p className="text-base font-semibold text-white">{policy.customer_name}</p>
        </div>
        <span className="text-xs bg-green-500/20 text-green-400 px-2 py-1 rounded-full font-medium">Active</span>
      </div>
      <div className="grid grid-cols-2 gap-2 mt-3">
        {[
          { label: 'Vehicle', value: `${policy.vehicle_make} ${policy.vehicle_model} (${policy.vehicle_year})` },
          { label: 'Reg. No.', value: policy.vehicle_reg_no },
          { label: 'Coverage', value: policy.coverage_type },
          { label: 'Sum Insured', value: `₹${Number(policy.sum_insured).toLocaleString('en-IN')}` },
          { label: 'Valid Until', value: policy.policy_end },
        ].map(f => (
          <div key={f.label} className="bg-slate-800/60 rounded-lg px-3 py-2">
            <p className="text-xs text-slate-500">{f.label}</p>
            <p className="text-sm text-slate-200">{f.value}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

function EvidenceTab({ id, label, badge, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
        active
          ? 'border-indigo-500 text-indigo-400'
          : 'border-transparent text-slate-500 hover:text-slate-300'
      }`}
    >
      {label}
      {badge && (
        <span className="text-[10px] bg-green-500/20 text-green-400 px-1.5 py-0.5 rounded-full font-semibold leading-none">
          {badge}
        </span>
      )}
    </button>
  )
}

export default function NewClaim() {
  const navigate = useNavigate()
  const { session } = useAuth()

  const [phone, setPhone] = useState('')
  const [looking, setLooking] = useState(false)
  const [lookupError, setLookupError] = useState('')
  const [policy, setPolicy] = useState(null)

  const [form, setForm] = useState({
    claim_type: 'Motor - Own Damage',
    incident_date: '',
    incident_location: '',
    description: '',
  })

  // Evidence state — three separate buckets
  const [evidenceTab, setEvidenceTab] = useState('photos')
  const [photos, setPhotos] = useState([])
  const [estimateFiles, setEstimateFiles] = useState([])
  const [garage, setGarage] = useState({ amount: '', workshop_name: '' })
  const [firFiles, setFirFiles] = useState([])
  const [fir, setFir] = useState({ number: '' })

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')

  const set = k => e => setForm(f => ({ ...f, [k]: e.target.value }))
  const setG = k => e => setGarage(g => ({ ...g, [k]: e.target.value }))

  const maxDatetime = new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
    .toISOString().slice(0, 16)

  const handleLookup = async () => {
    if (!phone.trim()) return
    setLooking(true)
    setLookupError('')
    setPolicy(null)
    try {
      const { data } = await api.get(`/customers/lookup?phone=${phone.trim()}`)
      setPolicy(data)
    } catch (err) {
      setLookupError(err.response?.data?.detail || 'No policy found for this number')
    } finally {
      setLooking(false)
    }
  }

  const handleSubmit = async e => {
    e.preventDefault()
    if (!policy) return
    setSubmitting(true)
    setSubmitError('')
    try {
      // 1. Create the claim record (with optional garage/FIR metadata)
      const fd = new FormData()
      fd.append('policy_no', policy.policy_no)
      fd.append('claimant', policy.customer_name)
      fd.append('phone', phone.trim())
      fd.append('vehicle', `${policy.vehicle_make} ${policy.vehicle_model} (${policy.vehicle_reg_no})`)
      fd.append('claim_type', form.claim_type)
      fd.append('incident_date', form.incident_date)
      fd.append('incident_location', form.incident_location)
      fd.append('description', form.description)
      if (garage.amount) fd.append('garage_estimate_amount', garage.amount)
      if (garage.workshop_name) fd.append('garage_workshop_name', garage.workshop_name)
      if (fir.number) fd.append('fir_number', fir.number)

      const { data: claim } = await api.post('/claims/', fd)

      // 2. Upload damage photos
      if (photos.length > 0) {
        const imgFd = new FormData()
        photos.forEach(f => imgFd.append('files', f))
        imgFd.append('doc_type', 'images')
        await api.post(`/claims/${claim.claim_id}/files`, imgFd)
      }

      // 3. Upload garage estimate document
      if (estimateFiles.length > 0) {
        const estFd = new FormData()
        estimateFiles.forEach(f => estFd.append('files', f))
        estFd.append('doc_type', 'estimate')
        await api.post(`/claims/${claim.claim_id}/files`, estFd)
      }

      // 4. Upload FIR document
      if (firFiles.length > 0) {
        const firFd = new FormData()
        firFiles.forEach(f => firFd.append('files', f))
        firFd.append('doc_type', 'fir')
        await api.post(`/claims/${claim.claim_id}/files`, firFd)
      }

      // Policyholder → confirmation page; adjudicator → straight to the dashboard
      if (session?.role === 'adjudicator') {
        navigate(`/claims/${claim.claim_id}`)
      } else {
        navigate(`/submitted/${claim.claim_id}`)
      }
    } catch (err) {
      setSubmitError(err.response?.data?.detail || 'Submission failed. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  const label = 'block text-sm font-medium text-slate-300 mb-1'
  const input = 'w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500'
  const fileInput = 'w-full text-slate-400 text-sm file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:bg-indigo-600 file:text-white file:text-sm file:cursor-pointer hover:file:bg-indigo-700'

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-2xl font-bold text-white mb-6">File a New Claim</h1>

      <div className="bg-slate-900 rounded-2xl border border-slate-700 p-6 space-y-5">

        {/* Step 1 — Phone lookup */}
        <div>
          <label className={label}>Your Registered Mobile Number</label>
          <div className="flex gap-2">
            <input
              className={`${input} flex-1`}
              placeholder="e.g. 9876543210"
              value={phone}
              onChange={e => { setPhone(e.target.value); setPolicy(null); setLookupError('') }}
              maxLength={10}
              onKeyDown={e => e.key === 'Enter' && handleLookup()}
            />
            <button
              type="button"
              onClick={handleLookup}
              disabled={looking || !phone.trim()}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors whitespace-nowrap"
            >
              {looking ? 'Finding...' : 'Find Policy'}
            </button>
          </div>
          {lookupError && <p className="text-red-400 text-sm mt-2">{lookupError}</p>}
        </div>

        {/* Step 2 — Policy card */}
        {policy && <PolicyCard policy={policy} />}

        {/* Step 3 — Claim details */}
        {policy && (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="border-t border-slate-700 pt-4">
              <p className="text-xs text-slate-500 uppercase tracking-wider mb-4">Incident Details</p>

              <div className="space-y-4">
                <div>
                  <label className={label}>Claim Type</label>
                  <select className={input} value={form.claim_type} onChange={set('claim_type')}>
                    <option>Motor - Own Damage</option>
                    <option>Motor - Third Party</option>
                    <option>Motor - Theft</option>
                  </select>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className={label}>Incident Date & Time</label>
                    <input
                      type="datetime-local"
                      className={input}
                      value={form.incident_date}
                      onChange={set('incident_date')}
                      max={maxDatetime}
                      required
                    />
                  </div>
                  <div>
                    <label className={label}>Incident Location</label>
                    <input className={input} placeholder="e.g. HSR Layout, Bengaluru" value={form.incident_location} onChange={set('incident_location')} required />
                  </div>
                </div>

                <div>
                  <label className={label}>What happened?</label>
                  <textarea
                    className={`${input} h-28 resize-none`}
                    placeholder="Describe the incident in detail — what happened, how the damage occurred, and any other vehicles involved..."
                    value={form.description}
                    onChange={set('description')}
                    required
                  />
                </div>

                {/* Supporting Evidence — 3-tab section */}
                <div className="border border-slate-700 rounded-xl overflow-hidden">
                  <div className="bg-slate-800/60 px-4 pt-3 pb-0">
                    <p className="text-xs text-slate-500 uppercase tracking-wider mb-3">Supporting Evidence</p>
                    <div className="flex border-b border-slate-700 -mx-4 px-4">
                      <EvidenceTab
                        id="photos"
                        label="Damage Photos"
                        badge={photos.length > 0 ? photos.length : null}
                        active={evidenceTab === 'photos'}
                        onClick={() => setEvidenceTab('photos')}
                      />
                      <EvidenceTab
                        id="estimate"
                        label="Garage Estimate"
                        badge={(estimateFiles.length > 0 || garage.amount) ? null : null}
                        active={evidenceTab === 'estimate'}
                        onClick={() => setEvidenceTab('estimate')}
                      />
                      <EvidenceTab
                        id="fir"
                        label="FIR Report"
                        active={evidenceTab === 'fir'}
                        onClick={() => setEvidenceTab('fir')}
                      />
                    </div>
                  </div>

                  <div className="p-4 space-y-3">
                    {/* Tab 1: Damage Photos */}
                    {evidenceTab === 'photos' && (
                      <div>
                        <p className="text-xs text-slate-500 mb-3">
                          Upload photos of vehicle damage. Required for AI visual assessment and repair estimation.
                        </p>
                        <input
                          type="file"
                          multiple
                          accept="image/*"
                          onChange={e => setPhotos(Array.from(e.target.files))}
                          className={fileInput}
                        />
                        {photos.length > 0 && (
                          <p className="text-xs text-green-400 mt-1.5">
                            {photos.length} photo{photos.length > 1 ? 's' : ''} selected
                          </p>
                        )}
                      </div>
                    )}

                    {/* Tab 2: Garage Estimate */}
                    {evidenceTab === 'estimate' && (
                      <div className="space-y-3">
                        <p className="text-xs text-slate-500">
                          Upload the repair estimate slip from the garage, or enter the amount manually. The AI will cross-check this against its own assessment.
                        </p>
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label className={label}>Estimate Amount (₹)</label>
                            <input
                              type="number"
                              className={input}
                              placeholder="e.g. 85000"
                              value={garage.amount}
                              onChange={setG('amount')}
                              min="0"
                            />
                          </div>
                          <div>
                            <label className={label}>Workshop Name</label>
                            <input
                              className={input}
                              placeholder="e.g. Ganesh Motors"
                              value={garage.workshop_name}
                              onChange={setG('workshop_name')}
                            />
                          </div>
                        </div>
                        <div>
                          <label className={label}>Upload Estimate Slip <span className="text-slate-500 font-normal">(PDF or image)</span></label>
                          <input
                            type="file"
                            accept="image/*,.pdf"
                            onChange={e => setEstimateFiles(Array.from(e.target.files))}
                            className={fileInput}
                          />
                          {estimateFiles.length > 0 && (
                            <p className="text-xs text-green-400 mt-1.5">{estimateFiles[0].name} selected</p>
                          )}
                        </div>
                      </div>
                    )}

                    {/* Tab 3: FIR Report */}
                    {evidenceTab === 'fir' && (
                      <div className="space-y-3">
                        <p className="text-xs text-slate-500">
                          FIR is mandatory for theft and third-party claims. Upload a copy and optionally enter the FIR number.
                        </p>
                        <div>
                          <label className={label}>FIR Number <span className="text-slate-500 font-normal">(optional)</span></label>
                          <input
                            className={input}
                            placeholder="e.g. FIR/2026/0123"
                            value={fir.number}
                            onChange={e => setFir({ number: e.target.value })}
                          />
                        </div>
                        <div>
                          <label className={label}>Upload FIR Copy <span className="text-slate-500 font-normal">(PDF or image)</span></label>
                          <input
                            type="file"
                            accept="image/*,.pdf"
                            onChange={e => setFirFiles(Array.from(e.target.files))}
                            className={fileInput}
                          />
                          {firFiles.length > 0 && (
                            <p className="text-xs text-green-400 mt-1.5">{firFiles[0].name} selected</p>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {submitError && <p className="text-red-400 text-sm">{submitError}</p>}

            <button
              type="submit"
              disabled={submitting}
              className="w-full py-3 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-semibold rounded-xl transition-colors"
            >
              {submitting ? 'Submitting...' : 'Submit Claim'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
