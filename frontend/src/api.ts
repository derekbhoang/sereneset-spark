export type ReviewStatus = 'draft' | 'in_review' | 'approved' | 'rejected'
export type AssetFormatValue = 'copy' | 'image' | 'video_concept'
export type GenerationInputRole =
  | 'product'
  | 'brand_reference'
  | 'style_reference'
  | 'source_creative'
  | 'avoid_reference'

export type GenerationInputFile = {
  file: File
  role?: GenerationInputRole
}

export type BrandAssetType =
  | 'logo'
  | 'product_image'
  | 'style_reference'
  | 'guideline'
  | 'font'
  | 'other'

export type BrandAssetDto = {
  id: string
  name: string
  asset_type: BrandAssetType
  description: string | null
  usage_guidance: string | null
  tags: string[]
  source_url: string | null
  storage_key: string
  filename: string
  content_type: string
  size_bytes: number
  sha256: string
  is_active: boolean
  created_at: string
  updated_at: string
}

export type BrandAssetCreateDto = {
  name: string
  asset_type: BrandAssetType
  description?: string | null
  usage_guidance?: string | null
  tags?: string[]
  source_url?: string | null
}

export type BrandAssetUpdateDto = {
  name?: string
  asset_type?: BrandAssetType
  description?: string | null
  usage_guidance?: string | null
  tags?: string[]
  source_url?: string | null
  is_active?: boolean
}

export type BrandAssetDownloadUrlDto = {
  brand_asset_id: string
  storage_key: string
  filename: string
  content_type: string
  size_bytes: number
  download_url: string
  expires_seconds: number
}

export type CampaignBrandAssetDto = {
  id: string
  campaign_id: string
  brand_asset_id: string
  role: string
  created_at: string
  brand_asset: BrandAssetDto
}

export type CampaignBrandAssetCreateDto = {
  brand_asset_id: string
  role?: string
}

export type BrandAssetFilters = {
  assetType?: BrandAssetType
  isActive?: boolean
  search?: string
  offset?: number
  limit?: number
}

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

export type CampaignCreateDto = {
  name: string
  product: string
  audience: string
  status?: string
  due_date?: string | null
  owner: string
  goal: string
  tone: string
  brief: string
  channels: string[]
  brand_inputs: string[]
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
  inputs: AssetVersionInputDto[]
}

export type AssetVersionInputDto = {
  id: string
  asset_version_id: string
  role: string
  storage_key: string
  filename: string
  content_type: string
  size_bytes: number
  sha256: string
  source: string
  storage_ownership: string
  brand_asset_id: string | null
  campaign_brand_asset_id: string | null
  brand_asset_type: string | null
  brand_asset_name: string | null
  usage_guidance: string | null
  created_at: string
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

export type VideoAspectRatio = '16:9' | '9:16' | '1:1'
export type VideoResolution = '720p' | '1080p'
export type GenerationJobKind = 'video'
export type GenerationJobStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'canceled'

export type VideoGenerationCreateDto = {
  title?: string | null
  channel: string
  prompt: string
  status?: 'draft'
  reviewer?: string | null
  tags?: string[]
  summary?: string | null
  model?: string | null
  duration_seconds?: number
  aspect_ratio?: VideoAspectRatio
  resolution?: VideoResolution
  source_version_id?: string | null
  source_brand_asset_id?: string | null
}

export type GenerationJobDto = {
  id: string
  asset_version_id: string
  kind: GenerationJobKind
  status: GenerationJobStatus
  provider: string
  model: string
  prompt: string
  parameters: Record<string, unknown>
  progress_percent: number
  provider_job_id: string | null
  attempt_count: number
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
  updated_at: string
}

export type VideoGenerationSubmissionDto = {
  asset: AssetDto
  job: GenerationJobDto
}

export type GenerationJobFilters = {
  status?: GenerationJobStatus
  offset?: number
  limit?: number
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

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000/api/v1'

async function readErrorMessage(response: Response): Promise<string> {
  let message = `Request failed with status ${response.status}`

  try {
    const body = (await response.json()) as {
      detail?: string | Array<{ msg?: string }>
    }
    if (typeof body.detail === 'string') {
      message = body.detail
    } else if (Array.isArray(body.detail)) {
      const validationMessages = body.detail
        .map((item) => item.msg)
        .filter((item): item is string => Boolean(item))
      if (validationMessages.length > 0) {
        message = validationMessages.join(', ')
      }
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

async function requestVoid(
  path: string,
  init: RequestInit = {},
): Promise<void> {
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

function buildGenerationInputFormData(
  payload: AssetGenerationCreateDto | AssetVersionGenerationCreateDto,
  inputs: GenerationInputFile[] = [],
): FormData {
  const formData = new FormData()

  formData.append('payload', JSON.stringify(payload))

  for (const input of inputs) {
    formData.append('files', input.file)
    formData.append('roles', input.role ?? 'style_reference')
  }

  return formData
}

export function fetchCampaigns(): Promise<CampaignDto[]> {
  return request<CampaignDto[]>('/campaigns')
}

export function createCampaign(
  campaign: CampaignCreateDto,
): Promise<CampaignDto> {
  return request<CampaignDto>('/campaigns', {
    method: 'POST',
    body: JSON.stringify(campaign),
  })
}

export function deleteCampaign(campaignId: string): Promise<void> {
  return requestVoid(`/campaigns/${campaignId}`, {
    method: 'DELETE',
  })
}

export function getCampaignExportUrl(campaignId: string): string {
  return `${API_BASE_URL}/campaigns/${encodeURIComponent(campaignId)}/export`
}

export function fetchBrandAssets(
  filters: BrandAssetFilters = {},
): Promise<BrandAssetDto[]> {
  const params = new URLSearchParams()

  if (filters.assetType) {
    params.set('asset_type', filters.assetType)
  }

  if (filters.isActive !== undefined) {
    params.set('is_active', String(filters.isActive))
  }

  if (filters.search) {
    params.set('search', filters.search)
  }

  if (filters.offset !== undefined) {
    params.set('offset', String(filters.offset))
  }

  if (filters.limit !== undefined) {
    params.set('limit', String(filters.limit))
  }

  const query = params.toString()
  return request<BrandAssetDto[]>(
    `/brand-assets${query ? `?${query}` : ''}`,
  )
}

export function fetchBrandAsset(brandAssetId: string): Promise<BrandAssetDto> {
  return request<BrandAssetDto>(`/brand-assets/${brandAssetId}`)
}

export function uploadBrandAsset(
  brandAsset: BrandAssetCreateDto,
  file: File,
): Promise<BrandAssetDto> {
  const formData = new FormData()
  formData.append('payload', JSON.stringify(brandAsset))
  formData.append('file', file)

  return uploadRequest<BrandAssetDto>('/brand-assets', formData)
}

export function updateBrandAsset(
  brandAssetId: string,
  brandAsset: BrandAssetUpdateDto,
): Promise<BrandAssetDto> {
  return request<BrandAssetDto>(`/brand-assets/${brandAssetId}`, {
    method: 'PATCH',
    body: JSON.stringify(brandAsset),
  })
}

export function archiveBrandAsset(brandAssetId: string): Promise<void> {
  return requestVoid(`/brand-assets/${brandAssetId}`, {
    method: 'DELETE',
  })
}

export function fetchBrandAssetDownloadUrl(
  brandAssetId: string,
  expiresSeconds = 3600,
): Promise<BrandAssetDownloadUrlDto> {
  const params = new URLSearchParams({
    expires_seconds: String(expiresSeconds),
  })

  return request<BrandAssetDownloadUrlDto>(
    `/brand-assets/${brandAssetId}/download-url?${params}`,
  )
}

export function fetchCampaignBrandAssets(
  campaignId: string,
): Promise<CampaignBrandAssetDto[]> {
  return request<CampaignBrandAssetDto[]>(
    `/campaigns/${campaignId}/brand-assets`,
  )
}

export function attachBrandAssetToCampaign(
  campaignId: string,
  attachment: CampaignBrandAssetCreateDto,
): Promise<CampaignBrandAssetDto> {
  return request<CampaignBrandAssetDto>(
    `/campaigns/${campaignId}/brand-assets`,
    {
      method: 'POST',
      body: JSON.stringify(attachment),
    },
  )
}

export function detachBrandAssetFromCampaign(
  campaignId: string,
  linkId: string,
): Promise<void> {
  return requestVoid(`/campaigns/${campaignId}/brand-assets/${linkId}`, {
    method: 'DELETE',
  })
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

export function generateCampaignAssetWithInputs(
  campaignId: string,
  asset: AssetGenerationCreateDto,
  inputs: GenerationInputFile[] = [],
): Promise<AssetDto> {
  return uploadRequest<AssetDto>(
    `/campaigns/${campaignId}/assets/generate-with-inputs`,
    buildGenerationInputFormData(asset, inputs),
  )
}

export function submitVideoGeneration(
  campaignId: string,
  video: VideoGenerationCreateDto,
): Promise<VideoGenerationSubmissionDto> {
  return request<VideoGenerationSubmissionDto>(
    `/campaigns/${campaignId}/assets/generate-video`,
    {
      method: 'POST',
      body: JSON.stringify(video),
    },
  )
}

export function fetchCampaignGenerationJobs(
  campaignId: string,
  filters: GenerationJobFilters = {},
  signal?: AbortSignal,
): Promise<GenerationJobDto[]> {
  const params = new URLSearchParams()

  if (filters.status) {
    params.set('status', filters.status)
  }

  if (filters.offset !== undefined) {
    params.set('offset', String(filters.offset))
  }

  if (filters.limit !== undefined) {
    params.set('limit', String(filters.limit))
  }

  const query = params.toString()
  return request<GenerationJobDto[]>(
    `/campaigns/${campaignId}/generation-jobs${query ? `?${query}` : ''}`,
    { signal },
  )
}

export function fetchCampaignGenerationJob(
  campaignId: string,
  jobId: string,
  signal?: AbortSignal,
): Promise<GenerationJobDto> {
  return request<GenerationJobDto>(
    `/campaigns/${campaignId}/generation-jobs/${jobId}`,
    { signal },
  )
}

export function cancelCampaignGenerationJob(
  campaignId: string,
  jobId: string,
): Promise<GenerationJobDto> {
  return request<GenerationJobDto>(
    `/campaigns/${campaignId}/generation-jobs/${jobId}/cancel`,
    { method: 'POST' },
  )
}

export function retryCampaignGenerationJob(
  campaignId: string,
  jobId: string,
): Promise<GenerationJobDto> {
  return request<GenerationJobDto>(
    `/campaigns/${campaignId}/generation-jobs/${jobId}/retry`,
    { method: 'POST' },
  )
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

export function generateAssetVersionWithInputs(
  assetId: string,
  version: AssetVersionGenerationCreateDto,
  inputs: GenerationInputFile[] = [],
): Promise<AssetDto> {
  return uploadRequest<AssetDto>(
    `/assets/${assetId}/versions/generate-with-inputs`,
    buildGenerationInputFormData(version, inputs),
  )
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
