import { useMemo, useState } from 'react'
import './App.css'

type ReviewStatus = 'draft' | 'in_review' | 'approved' | 'rejected'
type AssetFormat = 'Copy' | 'Image' | 'Video concept'

type Campaign = {
  id: string
  name: string
  product: string
  audience: string
  status: string
  due: string
  owner: string
  health: number
  goal: string
  tone: string
  channels: string[]
  brief: string
  brandInputs: string[]
}

type AssetVersion = {
  id: string
  created: string
  label: string
  prompt: string
  model: string
  storageKey: string
}

type Asset = {
  id: string
  campaignId: string
  title: string
  format: AssetFormat
  channel: string
  status: ReviewStatus
  updated: string
  reviewer: string
  tags: string[]
  copy: string
  preview: 'evergreen' | 'coral' | 'ink' | 'sun'
  versions: AssetVersion[]
}

const campaigns: Campaign[] = [
  {
    id: 'camp-summer-reset',
    name: 'Summer Reset Launch',
    product: 'SereneSet Essentials Kit',
    audience: 'Busy wellness shoppers, 28-44',
    status: 'Generating',
    due: 'Jul 12',
    owner: 'Mira Chen',
    health: 82,
    goal: 'Increase waitlist signups before the retail launch.',
    tone: 'Grounded, precise, calm',
    channels: ['Instagram', 'Email', 'Paid social', 'Landing page'],
    brief:
      'Introduce the essentials kit as a simple daily reset for people who want a calmer home routine without a complicated ritual.',
    brandInputs: ['Tone guide v3', 'Product claims', 'Usage disclaimers'],
  },
  {
    id: 'camp-retail-partner',
    name: 'Retail Partner Pitch',
    product: 'Wholesale discovery pack',
    audience: 'Boutique buyers and store owners',
    status: 'Review',
    due: 'Jul 18',
    owner: 'Noah Patel',
    health: 64,
    goal: 'Create polished sell-in assets for three regional buyers.',
    tone: 'Commercial, clear, assured',
    channels: ['Pitch deck', 'Email', 'One-sheet'],
    brief:
      'Turn the wholesale pack into concise sales assets that show margin potential, shelf appeal, and repeat purchase hooks.',
    brandInputs: ['Wholesale FAQ', 'Retail photography', 'Margin notes'],
  },
  {
    id: 'camp-membership-refresh',
    name: 'Membership Refresh',
    product: 'SereneSet Circle',
    audience: 'Existing customers and dormant subscribers',
    status: 'Drafting',
    due: 'Aug 03',
    owner: 'Lena Ortiz',
    health: 48,
    goal: 'Reposition the monthly membership around flexible routines.',
    tone: 'Warm, practical, lightly editorial',
    channels: ['Email', 'SMS', 'Customer portal'],
    brief:
      'Refresh membership messages so returning customers understand the value of choice, replenishment, and seasonal edits.',
    brandInputs: ['Lifecycle segments', 'Offer rules', 'Voice samples'],
  },
]

const initialAssets: Asset[] = [
  {
    id: 'asset-ig-carousel',
    campaignId: 'camp-summer-reset',
    title: 'Five-slide launch carousel',
    format: 'Image',
    channel: 'Instagram',
    status: 'in_review',
    updated: '10 min ago',
    reviewer: 'Avery',
    tags: ['launch', 'routine', 'visual'],
    copy:
      'A quiet sequence that opens on a sunlit counter, moves through three product-use moments, and closes with a waitlist callout.',
    preview: 'evergreen',
    versions: [
      {
        id: 'v3',
        created: 'Jun 29, 12:41',
        label: 'Softened product shadows and tightened CTA',
        prompt:
          'Create a calm wellness carousel concept for a summer reset kit using natural light and concise waitlist messaging.',
        model: 'gmi/image-campaign-v2',
        storageKey:
          'campaigns/camp-summer-reset/assets/asset-ig-carousel/versions/v3/preview.png',
      },
      {
        id: 'v2',
        created: 'Jun 29, 12:22',
        label: 'Added daily ritual framing',
        prompt:
          'Refine the carousel to emphasize a simple daily ritual and remove spa language.',
        model: 'gmi/image-campaign-v2',
        storageKey:
          'campaigns/camp-summer-reset/assets/asset-ig-carousel/versions/v2/preview.png',
      },
    ],
  },
  {
    id: 'asset-email-hero',
    campaignId: 'camp-summer-reset',
    title: 'Waitlist email hero copy',
    format: 'Copy',
    channel: 'Email',
    status: 'approved',
    updated: '42 min ago',
    reviewer: 'Mira',
    tags: ['email', 'waitlist', 'approved'],
    copy:
      'A calmer routine starts with fewer decisions. Meet the SereneSet Essentials Kit, a compact edit for resetting the tone of your space.',
    preview: 'coral',
    versions: [
      {
        id: 'v2',
        created: 'Jun 29, 12:08',
        label: 'Approved headline and preheader',
        prompt:
          'Write email hero copy for a wellness kit launch with calm, grounded language and no clinical claims.',
        model: 'openai/gpt-4.1',
        storageKey:
          'campaigns/camp-summer-reset/assets/asset-email-hero/versions/v2/copy.json',
      },
    ],
  },
  {
    id: 'asset-paid-social',
    campaignId: 'camp-summer-reset',
    title: 'Paid social concept trio',
    format: 'Image',
    channel: 'Paid social',
    status: 'draft',
    updated: '1 hr ago',
    reviewer: 'Unassigned',
    tags: ['paid', 'concept', 'testing'],
    copy:
      'Three square concepts test product-first, routine-first, and offer-first positioning for paid social acquisition.',
    preview: 'sun',
    versions: [
      {
        id: 'v1',
        created: 'Jun 29, 11:37',
        label: 'Initial testing concepts',
        prompt:
          'Generate three paid social image concepts with distinct positioning angles for the essentials kit.',
        model: 'gmi/image-campaign-v2',
        storageKey:
          'campaigns/camp-summer-reset/assets/asset-paid-social/versions/v1/concepts.json',
      },
    ],
  },
  {
    id: 'asset-buyer-email',
    campaignId: 'camp-retail-partner',
    title: 'Buyer outreach sequence',
    format: 'Copy',
    channel: 'Email',
    status: 'in_review',
    updated: 'Yesterday',
    reviewer: 'Noah',
    tags: ['retail', 'buyer', 'sequence'],
    copy:
      'A three-touch outreach flow that leads with category fit, follows with visual merchandising, and closes on low-risk trial terms.',
    preview: 'ink',
    versions: [
      {
        id: 'v1',
        created: 'Jun 28, 16:20',
        label: 'Initial wholesale flow',
        prompt:
          'Draft a three-email buyer outreach sequence for boutique retailers evaluating a premium wellness discovery pack.',
        model: 'openai/gpt-4.1',
        storageKey:
          'campaigns/camp-retail-partner/assets/asset-buyer-email/versions/v1/copy.json',
      },
    ],
  },
  {
    id: 'asset-member-sms',
    campaignId: 'camp-membership-refresh',
    title: 'Dormant member SMS set',
    format: 'Copy',
    channel: 'SMS',
    status: 'draft',
    updated: 'Yesterday',
    reviewer: 'Lena',
    tags: ['sms', 'retention', 'membership'],
    copy:
      'Short retention messages that frame membership as flexible replenishment instead of a fixed subscription.',
    preview: 'evergreen',
    versions: [
      {
        id: 'v1',
        created: 'Jun 28, 14:11',
        label: 'Initial retention messages',
        prompt:
          'Write five concise SMS options for dormant wellness subscribers with a practical, warm tone.',
        model: 'openai/gpt-4.1',
        storageKey:
          'campaigns/camp-membership-refresh/assets/asset-member-sms/versions/v1/copy.json',
      },
    ],
  },
]

const reviewStatuses: ReviewStatus[] = [
  'draft',
  'in_review',
  'approved',
  'rejected',
]

const statusLabels: Record<ReviewStatus, string> = {
  draft: 'Draft',
  in_review: 'In review',
  approved: 'Approved',
  rejected: 'Rejected',
}

const formatOptions: AssetFormat[] = ['Copy', 'Image', 'Video concept']

function App() {
  const [selectedCampaignId, setSelectedCampaignId] = useState(campaigns[0].id)
  const [assets, setAssets] = useState(initialAssets)
  const [selectedAssetId, setSelectedAssetId] = useState(initialAssets[0].id)
  const [statusFilter, setStatusFilter] = useState<ReviewStatus | 'all'>('all')
  const [channelFilter, setChannelFilter] = useState('All')
  const [requestFormat, setRequestFormat] = useState<AssetFormat>('Image')
  const [requestChannel, setRequestChannel] = useState('Instagram')
  const [requestPrompt, setRequestPrompt] = useState(
    'Generate a composed launch asset that keeps the product central and uses calm, benefit-led messaging.',
  )

  const selectedCampaign =
    campaigns.find((campaign) => campaign.id === selectedCampaignId) ??
    campaigns[0]

  const campaignAssets = useMemo(
    () => assets.filter((asset) => asset.campaignId === selectedCampaign.id),
    [assets, selectedCampaign.id],
  )

  const channels = useMemo(
    () => ['All', ...new Set(selectedCampaign.channels)],
    [selectedCampaign.channels],
  )

  const filteredAssets = campaignAssets.filter((asset) => {
    const matchesStatus =
      statusFilter === 'all' ? true : asset.status === statusFilter
    const matchesChannel =
      channelFilter === 'All' ? true : asset.channel === channelFilter

    return matchesStatus && matchesChannel
  })

  const selectedAsset =
    campaignAssets.find((asset) => asset.id === selectedAssetId) ??
    filteredAssets[0] ??
    campaignAssets[0]

  const approvedCount = campaignAssets.filter(
    (asset) => asset.status === 'approved',
  ).length

  function selectCampaign(campaignId: string) {
    const nextAsset = assets.find((asset) => asset.campaignId === campaignId)
    const nextCampaign = campaigns.find((campaign) => campaign.id === campaignId)

    setSelectedCampaignId(campaignId)
    setSelectedAssetId(nextAsset?.id ?? '')
    setStatusFilter('all')
    setChannelFilter('All')
    setRequestChannel(nextCampaign?.channels[0] ?? 'Instagram')
  }

  function updateAssetStatus(status: ReviewStatus) {
    if (!selectedAsset) {
      return
    }

    setAssets((currentAssets) =>
      currentAssets.map((asset) =>
        asset.id === selectedAsset.id ? { ...asset, status } : asset,
      ),
    )
  }

  function generateAsset() {
    const now = Date.now()
    const newAsset: Asset = {
      id: `asset-${now}`,
      campaignId: selectedCampaign.id,
      title: `${requestChannel} ${requestFormat.toLowerCase()} draft`,
      format: requestFormat,
      channel: requestChannel,
      status: 'draft',
      updated: 'Just now',
      reviewer: 'Unassigned',
      tags: ['generated', requestChannel.toLowerCase().replace(/\s/g, '-')],
      copy:
        requestFormat === 'Copy'
          ? 'A generated copy direction will appear here with headline, support copy, and compliance notes ready for review.'
          : 'A generated creative direction will appear here with composition, focal point, messaging, and production notes.',
      preview:
        requestFormat === 'Copy'
          ? 'ink'
          : requestChannel === 'Email'
            ? 'coral'
            : requestChannel === 'Paid social'
              ? 'sun'
              : 'evergreen',
      versions: [
        {
          id: 'v1',
          created: 'Just now',
          label: 'Initial generated draft',
          prompt: requestPrompt,
          model:
            requestFormat === 'Copy' ? 'openai/gpt-4.1' : 'gmi/image-campaign-v2',
          storageKey: `campaigns/${selectedCampaign.id}/assets/asset-${now}/versions/v1/${requestFormat === 'Copy' ? 'copy.json' : 'preview.png'}`,
        },
      ],
    }

    setAssets((currentAssets) => [newAsset, ...currentAssets])
    setSelectedAssetId(newAsset.id)
    setStatusFilter('all')
    setChannelFilter('All')
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup" aria-label="SereneSet Spark">
          <span className="brand-mark">SS</span>
          <div>
            <strong>SereneSet Spark</strong>
            <span>Campaign asset workspace</span>
          </div>
        </div>

        <nav className="top-nav" aria-label="Primary">
          <a href="#campaigns" aria-current="page">
            Campaigns
          </a>
          <a href="#assets">Assets</a>
          <a href="#library">Brand library</a>
          <a href="#exports">Exports</a>
        </nav>

        <div className="top-actions">
          <label className="search-field">
            <span>Search</span>
            <input type="search" placeholder="Asset, channel, tag" />
          </label>
          <button className="button button-secondary" type="button">
            Export pack
          </button>
        </div>
      </header>

      <div className="workspace" id="campaigns">
        <aside className="campaign-rail" aria-label="Campaigns">
          <div className="rail-heading">
            <span>Campaigns</span>
            <strong>{campaigns.length}</strong>
          </div>

          <div className="campaign-list">
            {campaigns.map((campaign) => (
              <button
                className={`campaign-card ${
                  campaign.id === selectedCampaign.id ? 'is-active' : ''
                }`}
                key={campaign.id}
                onClick={() => selectCampaign(campaign.id)}
                type="button"
              >
                <span className="campaign-card-top">
                  <strong>{campaign.name}</strong>
                  <span>{campaign.status}</span>
                </span>
                <span className="muted">{campaign.product}</span>
                <span className="campaign-meta">
                  <span>{campaign.due}</span>
                  <span>{campaign.owner}</span>
                </span>
                <span className="health-track" aria-hidden="true">
                  <span style={{ width: `${campaign.health}%` }} />
                </span>
              </button>
            ))}
          </div>
        </aside>

        <main className="campaign-stage">
          <section className="campaign-header" aria-labelledby="campaign-title">
            <div>
              <span className="eyebrow">{selectedCampaign.product}</span>
              <h1 id="campaign-title">{selectedCampaign.name}</h1>
              <p>{selectedCampaign.goal}</p>
            </div>

            <dl className="campaign-stats" aria-label="Campaign status">
              <div>
                <dt>Assets</dt>
                <dd>{campaignAssets.length}</dd>
              </div>
              <div>
                <dt>Approved</dt>
                <dd>{approvedCount}</dd>
              </div>
              <div>
                <dt>Due</dt>
                <dd>{selectedCampaign.due}</dd>
              </div>
            </dl>
          </section>

          <div className="work-grid">
            <section className="brief-panel" aria-labelledby="brief-heading">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">Brief</span>
                  <h2 id="brief-heading">Campaign context</h2>
                </div>
              </div>

              <label className="field">
                <span>Audience</span>
                <input defaultValue={selectedCampaign.audience} />
              </label>

              <label className="field">
                <span>Tone</span>
                <input defaultValue={selectedCampaign.tone} />
              </label>

              <label className="field">
                <span>Brief</span>
                <textarea defaultValue={selectedCampaign.brief} rows={5} />
              </label>

              <div className="brand-inputs">
                {selectedCampaign.brandInputs.map((input) => (
                  <span key={input}>{input}</span>
                ))}
              </div>

              <div className="generator">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">Generate</span>
                    <h2>New asset</h2>
                  </div>
                </div>

                <div className="segmented" aria-label="Asset format">
                  {formatOptions.map((format) => (
                    <button
                      aria-pressed={requestFormat === format}
                      className={requestFormat === format ? 'is-selected' : ''}
                      key={format}
                      onClick={() => setRequestFormat(format)}
                      type="button"
                    >
                      {format}
                    </button>
                  ))}
                </div>

                <label className="field">
                  <span>Channel</span>
                  <select
                    onChange={(event) => setRequestChannel(event.target.value)}
                    value={requestChannel}
                  >
                    {selectedCampaign.channels.map((channel) => (
                      <option key={channel}>{channel}</option>
                    ))}
                  </select>
                </label>

                <label className="field">
                  <span>Prompt</span>
                  <textarea
                    onChange={(event) => setRequestPrompt(event.target.value)}
                    rows={4}
                    value={requestPrompt}
                  />
                </label>

                <button
                  className="button button-primary"
                  onClick={generateAsset}
                  type="button"
                >
                  Generate asset
                </button>
              </div>
            </section>

            <section className="asset-board" id="assets" aria-labelledby="assets-heading">
              <div className="board-toolbar">
                <div>
                  <span className="eyebrow">Assets</span>
                  <h2 id="assets-heading">Review queue</h2>
                </div>

                <div className="filters">
                  <select
                    aria-label="Filter by channel"
                    onChange={(event) => setChannelFilter(event.target.value)}
                    value={channelFilter}
                  >
                    {channels.map((channel) => (
                      <option key={channel}>{channel}</option>
                    ))}
                  </select>

                  <select
                    aria-label="Filter by status"
                    onChange={(event) =>
                      setStatusFilter(event.target.value as ReviewStatus | 'all')
                    }
                    value={statusFilter}
                  >
                    <option value="all">All statuses</option>
                    {reviewStatuses.map((status) => (
                      <option key={status} value={status}>
                        {statusLabels[status]}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="asset-grid">
                {filteredAssets.map((asset) => (
                  <button
                    className={`asset-card ${
                      selectedAsset?.id === asset.id ? 'is-active' : ''
                    }`}
                    key={asset.id}
                    onClick={() => setSelectedAssetId(asset.id)}
                    type="button"
                  >
                    <span className={`asset-preview ${asset.preview}`}>
                      <span className="preview-band" />
                      <span className="preview-copy" />
                      <span className="preview-chip" />
                    </span>
                    <span className="asset-card-body">
                      <span className="asset-row">
                        <strong>{asset.title}</strong>
                        <span className={`status-pill ${asset.status}`}>
                          {statusLabels[asset.status]}
                        </span>
                      </span>
                      <span className="asset-copy">{asset.copy}</span>
                      <span className="asset-foot">
                        <span>{asset.format}</span>
                        <span>{asset.channel}</span>
                        <span>{asset.updated}</span>
                      </span>
                    </span>
                  </button>
                ))}
              </div>

              {filteredAssets.length === 0 && (
                <div className="empty-state">No assets match these filters.</div>
              )}
            </section>

            <aside className="detail-panel" aria-label="Selected asset">
              {selectedAsset ? (
                <>
                  <div className="panel-heading">
                    <div>
                      <span className="eyebrow">Selected</span>
                      <h2>{selectedAsset.title}</h2>
                    </div>
                    <span className={`status-pill ${selectedAsset.status}`}>
                      {statusLabels[selectedAsset.status]}
                    </span>
                  </div>

                  <div className={`detail-preview ${selectedAsset.preview}`}>
                    <span />
                    <strong>{selectedAsset.format}</strong>
                  </div>

                  <p className="detail-copy">{selectedAsset.copy}</p>

                  <div className="status-controls" aria-label="Review status">
                    {reviewStatuses.map((status) => (
                      <button
                        aria-pressed={selectedAsset.status === status}
                        className={
                          selectedAsset.status === status ? 'is-selected' : ''
                        }
                        key={status}
                        onClick={() => updateAssetStatus(status)}
                        type="button"
                      >
                        {statusLabels[status]}
                      </button>
                    ))}
                  </div>

                  <dl className="metadata-list">
                    <div>
                      <dt>Reviewer</dt>
                      <dd>{selectedAsset.reviewer}</dd>
                    </div>
                    <div>
                      <dt>Channel</dt>
                      <dd>{selectedAsset.channel}</dd>
                    </div>
                    <div>
                      <dt>Tags</dt>
                      <dd>{selectedAsset.tags.join(', ')}</dd>
                    </div>
                  </dl>

                  <div className="version-list">
                    <h3>Versions</h3>
                    {selectedAsset.versions.map((version) => (
                      <div className="version-row" key={version.id}>
                        <span>
                          <strong>{version.id.toUpperCase()}</strong>
                          {version.label}
                        </span>
                        <span>{version.created}</span>
                        <code>{version.storageKey}</code>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div className="empty-state">No asset selected.</div>
              )}
            </aside>
          </div>
        </main>
      </div>
    </div>
  )
}

export default App
