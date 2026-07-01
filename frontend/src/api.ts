export type ReviewStatus = 'draft' | 'in_review' | 'approved' | 'rejected'
export type AssetFormatValue = 'copy' | 'image' | 'video_concept'

export type CampaignDto = {
  id: string
  name: string
  product: string
  audience: string
  status: string
  due_date: string | null
  owner: string
  goal: string
  tone: string
  brief: string
  channels: string[]
  brand_inputs: string[]
  created_at: string
  updated_at: string
}

export type AssetVersionDto = {
  id: string
  asset_id: string
  version_number: number
  label: string
  prompt: string
  model: string
  provider: string
  storage_key: string
  artifact_storage_key: string | null
  artifact_filename: string | null
  artifact_content_type: string | null
  artifact_size_bytes: number | null
  generation_metadata: Record<string, unknown>
}

export type AssetDto = {
  id: string
  campaign_id: string
  title: string
  format: AssetFormatValue
  channel: string
  status: ReviewStatus
  reviewer: string | null
  tags: string[]
  summary: string
  created_at: string
  updated_at: string
  versions: AssetVersionDto[]
}

export type AssetVersionCreateDto = {
  version_number: number
  label: string
  prompt: string
  model: string
  provider: string
  generation_metadata?: Record<string, unknown>
}

export type AssetCreateDto = {
  title: string
  format: AssetFormatValue
  channel: string
  status: ReviewStatus
  reviewer?: string | null
  tags: string[]
  summary: string
  initial_version?: AssetVersionCreateDto | null
}

export type AssetGenerationCreateDto = {
  title?: string | null
  format: AssetFormatValue
  channel: string
  prompt: string
  status?: ReviewStatus
  reviewer?: string | null
  tags?: string[]
  summary?: string | null
  model?: string | null
  generation_parameters?: Record<string, unknown>
  timeout_seconds?: number | null
}

export type AssetVersionGenerationCreateDto = {
  prompt: string
  label?: string | null
  model?: string | null
  generation_parameters?: Record<string, unknown>
  timeout_seconds?: number | null
}

export type AssetVersionDownloadUrlDto = {
  asset_id: string
  version_id: string
  storage_key: string
  download_url: string
  expires_seconds: number
}

export type AssetVersionArtifactDownloadUrlDto = {
  asset_id: string
  version_id: string
  artifact_storage_key: string
  artifact_filename: string | null
  artifact_content_type: string | null
  artifact_size_bytes: number | null
  download_url: string
  expires_seconds: number
}

export type DownloadedFile = {
  blob: Blob
  filename: string
}

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000/api/v1'

async function readErrorMessage(response: Response): Promise<string> {
  let message = `Request failed with status ${response.status}`

  try {
    const body = (await response.json()) as { detail?: string }
    if (body.detail) {
      message = body.detail
    }
  } catch {
    // Keep the status-based fallback when the response is not JSON.
  }

  return message
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init.headers,
    },
  })

  if (!response.ok) {
    throw new Error(await readErrorMessage(response))
  }

  return response.json() as Promise<T>
}

async function uploadRequest<T>(path: string, formData: FormData): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    body: formData,
  })

  if (!response.ok) {
    throw new Error(await readErrorMessage(response))
  }

  return response.json() as Promise<T>
}

function getFilenameFromContentDisposition(value: string | null): string | null {
  if (!value) {
    return null
  }

  const match = value.match(/filename="?([^"]+)"?/i)
  return match?.[1] ?? null
}

async function downloadRequest(
  path: string,
  fallbackFilename: string,
): Promise<DownloadedFile> {
  const response = await fetch(`${API_BASE_URL}${path}`)

  if (!response.ok) {
    throw new Error(await readErrorMessage(response))
  }

  const filename =
    getFilenameFromContentDisposition(
      response.headers.get('Content-Disposition'),
    ) ?? fallbackFilename

  return {
    blob: await response.blob(),
    filename,
  }
}

export function fetchCampaigns(): Promise<CampaignDto[]> {
  return request<CampaignDto[]>('/campaigns')
}

export function exportCampaignPack(campaignId: string): Promise<DownloadedFile> {
  return downloadRequest(`/campaigns/${campaignId}/export`, 'campaign-export.zip')
}

export function fetchCampaignAssets(
  campaignId: string,
  filters: { status?: ReviewStatus; channel?: string } = {},
): Promise<AssetDto[]> {
  const params = new URLSearchParams()

  if (filters.status) {
    params.set('status', filters.status)
  }

  if (filters.channel) {
    params.set('channel', filters.channel)
  }

  const query = params.toString()
  return request<AssetDto[]>(
    `/campaigns/${campaignId}/assets${query ? `?${query}` : ''}`,
  )
}

export function fetchAsset(assetId: string): Promise<AssetDto> {
  return request<AssetDto>(`/assets/${assetId}`)
}

export function createCampaignAsset(
  campaignId: string,
  asset: AssetCreateDto,
): Promise<AssetDto> {
  return request<AssetDto>(`/campaigns/${campaignId}/assets`, {
    method: 'POST',
    body: JSON.stringify(asset),
  })
}

export function generateCampaignAsset(
  campaignId: string,
  asset: AssetGenerationCreateDto,
): Promise<AssetDto> {
  return request<AssetDto>(`/campaigns/${campaignId}/assets/generate`, {
    method: 'POST',
    body: JSON.stringify(asset),
  })
}

export function updateAssetStatus(
  assetId: string,
  status: ReviewStatus,
): Promise<AssetDto> {
  return request<AssetDto>(`/assets/${assetId}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export function fetchAssetVersions(assetId: string): Promise<AssetVersionDto[]> {
  return request<AssetVersionDto[]>(`/assets/${assetId}/versions`)
}

export function createAssetVersion(
  assetId: string,
  version: AssetVersionCreateDto,
): Promise<AssetVersionDto> {
  return request<AssetVersionDto>(`/assets/${assetId}/versions`, {
    method: 'POST',
    body: JSON.stringify(version),
  })
}

export function generateAssetVersion(
  assetId: string,
  version: AssetVersionGenerationCreateDto,
): Promise<AssetDto> {
  return request<AssetDto>(`/assets/${assetId}/versions/generate`, {
    method: 'POST',
    body: JSON.stringify(version),
  })
}

export function fetchAssetVersionDownloadUrl(
  assetId: string,
  versionId: string,
  expiresSeconds = 3600,
): Promise<AssetVersionDownloadUrlDto> {
  const params = new URLSearchParams({
    expires_seconds: String(expiresSeconds),
  })

  return request<AssetVersionDownloadUrlDto>(
    `/assets/${assetId}/versions/${versionId}/download-url?${params}`,
  )
}

export function uploadAssetVersionArtifact(
  assetId: string,
  versionId: string,
  file: File,
): Promise<AssetVersionDto> {
  const formData = new FormData()
  formData.append('file', file)

  return uploadRequest<AssetVersionDto>(
    `/assets/${assetId}/versions/${versionId}/artifact`,
    formData,
  )
}

export function fetchAssetVersionArtifactDownloadUrl(
  assetId: string,
  versionId: string,
  expiresSeconds = 3600,
): Promise<AssetVersionArtifactDownloadUrlDto> {
  const params = new URLSearchParams({
    expires_seconds: String(expiresSeconds),
  })

  return request<AssetVersionArtifactDownloadUrlDto>(
    `/assets/${assetId}/versions/${versionId}/artifact/download-url?${params}`,
  )
}
