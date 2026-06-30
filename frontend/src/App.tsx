import { useEffect, useMemo, useState } from 'react'
import {
  createCampaignAsset,
  fetchAssetVersionDownloadUrl,
  fetchCampaignAssets,
  fetchCampaigns,
  updateAssetStatus as patchAssetStatus,
  type AssetCreateDto,
  type AssetDto,
  type AssetFormatValue,
  type AssetVersionDto,
  type CampaignDto,
  type ReviewStatus,
} from './api'
import './App.css'

type AssetFormat = 'Copy' | 'Image' | 'Video concept'
type PreviewName = 'evergreen' | 'coral' | 'ink' | 'sun'

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
  versionId: string
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
  preview: PreviewName
  versions: AssetVersion[]
}

const defaultPrompt =
  'Generate a composed launch asset that keeps the product central and uses calm, benefit-led messaging.'

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

const formatLabels: Record<AssetFormatValue, AssetFormat> = {
  copy: 'Copy',
  image: 'Image',
  video_concept: 'Video concept',
}

const formatValues: Record<AssetFormat, AssetFormatValue> = {
  Copy: 'copy',
  Image: 'image',
  'Video concept': 'video_concept',
}

function formatDueDate(value: string | null): string {
  if (!value) {
    return 'No due date'
  }

  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(year, month - 1, day)

  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  })
}

function formatTimestamp(value: string): string {
  const date = new Date(value)

  if (Number.isNaN(date.getTime())) {
    return 'Recently'
  }

  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function titleCase(value: string): string {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function campaignHealth(status: string, index: number): number {
  const normalizedStatus = status.toLowerCase()

  if (normalizedStatus.includes('approved')) {
    return 92
  }

  if (normalizedStatus.includes('review')) {
    return 70
  }

  if (normalizedStatus.includes('generat')) {
    return 82
  }

  if (normalizedStatus.includes('draft')) {
    return 48
  }

  return Math.min(86, 58 + index * 8)
}

function previewForAsset(format: AssetFormat, channel: string): PreviewName {
  if (format === 'Copy') {
    return 'ink'
  }

  if (channel === 'Email') {
    return 'coral'
  }

  if (channel === 'Paid social') {
    return 'sun'
  }

  return 'evergreen'
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong'
}

function mapCampaign(campaign: CampaignDto, index: number): Campaign {
  return {
    id: campaign.id,
    name: campaign.name,
    product: campaign.product,
    audience: campaign.audience,
    status: titleCase(campaign.status),
    due: formatDueDate(campaign.due_date),
    owner: campaign.owner,
    health: campaignHealth(campaign.status, index),
    goal: campaign.goal,
    tone: campaign.tone,
    channels: campaign.channels,
    brief: campaign.brief,
    brandInputs: campaign.brand_inputs,
  }
}

function mapAssetVersion(version: AssetVersionDto): AssetVersion {
  return {
    id: `v${version.version_number}`,
    versionId: version.id,
    created: version.provider,
    label: version.label,
    prompt: version.prompt,
    model: version.model,
    storageKey: version.storage_key,
  }
}

function mapAsset(asset: AssetDto): Asset {
  const format = formatLabels[asset.format]

  return {
    id: asset.id,
    campaignId: asset.campaign_id,
    title: asset.title,
    format,
    channel: asset.channel,
    status: asset.status,
    updated: formatTimestamp(asset.updated_at),
    reviewer: asset.reviewer ?? 'Unassigned',
    tags: asset.tags,
    copy: asset.summary,
    preview: previewForAsset(format, asset.channel),
    versions: asset.versions.map(mapAssetVersion),
  }
}

function App() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [assets, setAssets] = useState<Asset[]>([])
  const [selectedCampaignId, setSelectedCampaignId] = useState('')
  const [selectedAssetId, setSelectedAssetId] = useState('')
  const [statusFilter, setStatusFilter] = useState<ReviewStatus | 'all'>('all')
  const [channelFilter, setChannelFilter] = useState('All')
  const [requestFormat, setRequestFormat] = useState<AssetFormat>('Image')
  const [requestChannel, setRequestChannel] = useState('')
  const [requestPrompt, setRequestPrompt] = useState(defaultPrompt)
  const [isLoadingCampaigns, setIsLoadingCampaigns] = useState(true)
  const [isLoadingAssets, setIsLoadingAssets] = useState(false)
  const [isGenerating, setIsGenerating] = useState(false)
  const [isSavingStatus, setIsSavingStatus] = useState(false)
  const [openingVersionId, setOpeningVersionId] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    let isCancelled = false

    async function loadCampaigns() {
      setIsLoadingCampaigns(true)
      setErrorMessage(null)

      try {
        const campaignDtos = await fetchCampaigns()
        const nextCampaigns = campaignDtos.map(mapCampaign)

        if (isCancelled) {
          return
        }

        setCampaigns(nextCampaigns)
        setSelectedCampaignId(nextCampaigns[0]?.id ?? '')
        setRequestChannel(nextCampaigns[0]?.channels[0] ?? '')
      } catch (error) {
        if (!isCancelled) {
          setErrorMessage(getErrorMessage(error))
        }
      } finally {
        if (!isCancelled) {
          setIsLoadingCampaigns(false)
        }
      }
    }

    void loadCampaigns()

    return () => {
      isCancelled = true
    }
  }, [])

  const selectedCampaign = useMemo(
    () =>
      campaigns.find((campaign) => campaign.id === selectedCampaignId) ?? null,
    [campaigns, selectedCampaignId],
  )

  useEffect(() => {
    let isCancelled = false

    async function loadAssets(campaignId: string) {
      setIsLoadingAssets(true)
      setErrorMessage(null)

      try {
        const assetDtos = await fetchCampaignAssets(campaignId)
        const nextAssets = assetDtos.map(mapAsset)

        if (isCancelled) {
          return
        }

        setAssets(nextAssets)
        setSelectedAssetId((currentAssetId) => {
          if (
            currentAssetId &&
            nextAssets.some((asset) => asset.id === currentAssetId)
          ) {
            return currentAssetId
          }

          return nextAssets[0]?.id ?? ''
        })
      } catch (error) {
        if (!isCancelled) {
          setAssets([])
          setSelectedAssetId('')
          setErrorMessage(getErrorMessage(error))
        }
      } finally {
        if (!isCancelled) {
          setIsLoadingAssets(false)
        }
      }
    }

    if (!selectedCampaignId) {
      return
    }

    void loadAssets(selectedCampaignId)

    return () => {
      isCancelled = true
    }
  }, [selectedCampaignId])

  const campaignAssets = assets

  const channels = useMemo(
    () => ['All', ...(selectedCampaign?.channels ?? [])],
    [selectedCampaign],
  )

  const filteredAssets = campaignAssets.filter((asset) => {
    const matchesStatus =
      statusFilter === 'all' ? true : asset.status === statusFilter
    const matchesChannel =
      channelFilter === 'All' ? true : asset.channel === channelFilter

    return matchesStatus && matchesChannel
  })

  const selectedAsset =
    filteredAssets.find((asset) => asset.id === selectedAssetId) ??
    filteredAssets[0] ??
    null

  const approvedCount = campaignAssets.filter(
    (asset) => asset.status === 'approved',
  ).length

  function selectCampaign(campaignId: string) {
    if (campaignId === selectedCampaignId) {
      return
    }

    const nextCampaign = campaigns.find((campaign) => campaign.id === campaignId)

    setSelectedCampaignId(campaignId)
    setSelectedAssetId('')
    setAssets([])
    setStatusFilter('all')
    setChannelFilter('All')
    setRequestChannel(nextCampaign?.channels[0] ?? '')
  }

  async function updateAssetStatus(status: ReviewStatus) {
    if (!selectedAsset) {
      return
    }

    setIsSavingStatus(true)
    setErrorMessage(null)

    try {
      const updatedAsset = mapAsset(await patchAssetStatus(selectedAsset.id, status))
      setAssets((currentAssets) =>
        currentAssets.map((asset) =>
          asset.id === updatedAsset.id ? updatedAsset : asset,
        ),
      )
      setSelectedAssetId(updatedAsset.id)
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setIsSavingStatus(false)
    }
  }

  async function generateAsset() {
    if (!selectedCampaign || !requestChannel) {
      return
    }

    const now = Date.now()
    const formatValue = formatValues[requestFormat]
    const isCopy = requestFormat === 'Copy'
    const model = isCopy ? 'openai/gpt-4.1' : 'gmi/image-campaign-v2'
    const provider = isCopy ? 'openai' : 'gmi'
    const summary = isCopy
      ? 'A generated copy direction with headline, support copy, and compliance notes ready for review.'
      : 'A generated creative direction with composition, focal point, messaging, and production notes.'

    const assetPayload: AssetCreateDto = {
      title: `${requestChannel} ${requestFormat.toLowerCase()} draft`,
      format: formatValue,
      channel: requestChannel,
      status: 'draft',
      reviewer: null,
      tags: ['generated', requestChannel.toLowerCase().replace(/\s/g, '-')],
      summary,
      initial_version: {
        version_number: 1,
        label: 'Initial generated draft',
        prompt: requestPrompt,
        model,
        provider,
        generation_metadata: {
          channel: requestChannel,
          format: formatValue,
          requested_at_ms: now,
          source: 'frontend_mock_generation',
        },
      },
    }

    setIsGenerating(true)
    setErrorMessage(null)

    try {
      const createdAsset = mapAsset(
        await createCampaignAsset(selectedCampaign.id, assetPayload),
      )
      setAssets((currentAssets) => [
        createdAsset,
        ...currentAssets.filter((asset) => asset.id !== createdAsset.id),
      ])
      setSelectedAssetId(createdAsset.id)
      setStatusFilter('all')
      setChannelFilter('All')
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setIsGenerating(false)
    }
  }

  async function openStoredMetadata(version: AssetVersion) {
    if (!selectedAsset) {
      return
    }

    setOpeningVersionId(version.versionId)
    setErrorMessage(null)

    try {
      const download = await fetchAssetVersionDownloadUrl(
        selectedAsset.id,
        version.versionId,
      )
      const openedWindow = window.open(
        download.download_url,
        '_blank',
        'noopener,noreferrer',
      )

      if (!openedWindow) {
        setErrorMessage('The browser blocked the metadata tab')
      }
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setOpeningVersionId(null)
    }
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

      {errorMessage && (
        <div className="system-banner" role="alert">
          {errorMessage}
        </div>
      )}

      <div className="workspace" id="campaigns">
        <aside className="campaign-rail" aria-label="Campaigns">
          <div className="rail-heading">
            <span>Campaigns</span>
            <strong>{isLoadingCampaigns ? '...' : campaigns.length}</strong>
          </div>

          <div className="campaign-list">
            {campaigns.map((campaign) => (
              <button
                className={`campaign-card ${
                  campaign.id === selectedCampaignId ? 'is-active' : ''
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

          {!isLoadingCampaigns && campaigns.length === 0 && (
            <div className="empty-state">No campaigns found.</div>
          )}
        </aside>

        {selectedCampaign ? (
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
                  <input
                    defaultValue={selectedCampaign.audience}
                    key={`${selectedCampaign.id}-audience`}
                  />
                </label>

                <label className="field">
                  <span>Tone</span>
                  <input
                    defaultValue={selectedCampaign.tone}
                    key={`${selectedCampaign.id}-tone`}
                  />
                </label>

                <label className="field">
                  <span>Brief</span>
                  <textarea
                    defaultValue={selectedCampaign.brief}
                    key={`${selectedCampaign.id}-brief`}
                    rows={5}
                  />
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
                    disabled={isGenerating || !requestChannel}
                    onClick={generateAsset}
                    type="button"
                  >
                    {isGenerating ? 'Generating...' : 'Generate asset'}
                  </button>
                </div>
              </section>

              <section
                className="asset-board"
                id="assets"
                aria-labelledby="assets-heading"
              >
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
                        setStatusFilter(
                          event.target.value as ReviewStatus | 'all',
                        )
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

                {isLoadingAssets ? (
                  <div className="empty-state">Loading assets...</div>
                ) : (
                  <>
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
                      <div className="empty-state">
                        No assets match these filters.
                      </div>
                    )}
                  </>
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
                          disabled={isSavingStatus}
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
                        <div className="version-row" key={version.versionId}>
                          <span>
                            <strong>{version.id.toUpperCase()}</strong>
                            {version.label}
                          </span>
                          <span>{version.created}</span>
                          <code>{version.storageKey}</code>
                          <button
                            className="metadata-button"
                            disabled={openingVersionId === version.versionId}
                            onClick={() => openStoredMetadata(version)}
                            type="button"
                          >
                            {openingVersionId === version.versionId
                              ? 'Opening...'
                              : 'Open stored metadata'}
                          </button>
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
        ) : (
          <main className="campaign-stage">
            <div className="empty-state workspace-empty">
              {isLoadingCampaigns
                ? 'Loading workspace...'
                : 'No campaigns yet. Create one through the API to begin.'}
            </div>
          </main>
        )}
      </div>
    </div>
  )
}

export default App
