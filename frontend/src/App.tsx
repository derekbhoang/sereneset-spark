import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import {
  attachBrandAssetToCampaign,
  createCampaign,
  deleteCampaign,
  detachBrandAssetFromCampaign,
  exportCampaignPack,
  fetchBrandAssetDownloadUrl,
  fetchBrandAssets,
  fetchAsset,
  fetchAssetVersionArtifactDownloadUrl,
  fetchAssetVersionDownloadUrl,
  fetchCampaignAssets,
  fetchCampaignBrandAssets,
  fetchCampaigns,
  generateAssetVersion,
  generateAssetVersionWithInputs,
  generateCampaignAsset,
  generateCampaignAssetWithInputs,
  updateAssetStatus as patchAssetStatus,
  uploadBrandAsset,
  uploadAssetVersionArtifact,
  type AssetDto,
  type AssetFormatValue,
  type AssetGenerationCreateDto,
  type AssetVersionDto,
  type AssetVersionGenerationCreateDto,
  type AssetVersionInputDto,
  type BrandAssetDto,
  type BrandAssetType,
  type CampaignDto,
  type CampaignBrandAssetDto,
  type GenerationInputFile,
  type GenerationInputRole,
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
  versionNumber: number
  created: string
  label: string
  prompt: string
  model: string
  provider: string
  storageKey: string
  artifactStorageKey: string | null
  artifactFilename: string | null
  artifactContentType: string | null
  artifactSizeBytes: number | null
  generationMetadata: Record<string, unknown>
  generatedPreview: GeneratedPreview | null
  inputAssets: VersionInputAsset[]
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

type ArtifactPreviewUrl = {
  storageKey: string
  url: string
}

type GeneratedPreview = {
  url: string | null
  storageKey: string | null
  contentType: string | null
  filename: string | null
}

type CampaignFormState = {
  name: string
  product: string
  audience: string
  status: string
  dueDate: string
  owner: string
  goal: string
  tone: string
  brief: string
  channels: string
  brandInputs: string
}

type ReferenceImage = {
  id: string
  file: File
  role: GenerationInputRole
  previewUrl: string
}

type VersionInputAsset = {
  id: string | null
  role: string | null
  storageKey: string | null
  filename: string | null
  contentType: string | null
  sizeBytes: number | null
  sha256: string | null
  source: string | null
  brandAssetId: string | null
  brandAssetName: string | null
  usageGuidance: string | null
}

type BrandAssetFormState = {
  name: string
  assetType: BrandAssetType
  description: string
  usageGuidance: string
  tags: string
  sourceUrl: string
}

const defaultPrompt =
  'Generate a composed launch asset that keeps the product central and uses calm, benefit-led messaging.'

const defaultCampaignForm: CampaignFormState = {
  name: '',
  product: '',
  audience: '',
  status: 'drafting',
  dueDate: '',
  owner: '',
  goal: '',
  tone: 'Calm, benefit-led',
  brief: '',
  channels: 'Paid social, Email',
  brandInputs: '',
}

const defaultBrandAssetForm: BrandAssetFormState = {
  name: '',
  assetType: 'logo',
  description: '',
  usageGuidance: '',
  tags: '',
  sourceUrl: '',
}

const brandAssetTypeOptions: Array<{
  value: BrandAssetType
  label: string
}> = [
  { value: 'logo', label: 'Logo' },
  { value: 'product_image', label: 'Product image' },
  { value: 'style_reference', label: 'Style reference' },
  { value: 'guideline', label: 'Guideline' },
  { value: 'font', label: 'Font' },
  { value: 'other', label: 'Other' },
]

const campaignBrandRoleOptions = [
  { value: 'primary_logo', label: 'Primary logo' },
  { value: 'product', label: 'Product' },
  { value: 'style_reference', label: 'Style reference' },
  { value: 'brand_reference', label: 'Brand reference' },
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

const referenceRoleOptions: GenerationInputRole[] = [
  'product',
  'brand_reference',
  'style_reference',
  'source_creative',
  'avoid_reference',
]

const referenceRoleLabels: Record<GenerationInputRole, string> = {
  product: 'Product',
  brand_reference: 'Brand reference',
  style_reference: 'Style reference',
  source_creative: 'Source creative',
  avoid_reference: 'Avoid reference',
}

const maxReferenceImageCount = 5
const maxReferenceImageSizeBytes = 25 * 1024 * 1024
const allowedReferenceImageTypes = ['image/png', 'image/jpeg', 'image/webp']

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

function formatFileSize(value: number | null): string {
  if (value === null) {
    return 'Size pending'
  }

  if (value < 1024) {
    return `${value} B`
  }

  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KB`
  }

  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

function formatArtifactDetails(version: AssetVersion): string {
  const details = [
    version.artifactContentType ?? version.generatedPreview?.contentType,
    version.artifactSizeBytes === null
      ? null
      : formatFileSize(version.artifactSizeBytes),
  ].filter(Boolean)

  return details.join(' / ') || 'Stored artifact'
}

function getVersionFilename(version: AssetVersion): string | null {
  return version.artifactFilename ?? version.generatedPreview?.filename ?? null
}

function getFileExtension(filename: string | null): string {
  const extension = filename?.split('.').pop()

  if (!extension || extension === filename) {
    return 'file'
  }

  return extension.slice(0, 5).toLowerCase()
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function readBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null
}

function readNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function readAssetMetadataList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    const stringValue = readString(value)

    if (stringValue) {
      return stringValue
    }
  }

  return null
}

function displayValue(value: string | null): string {
  return value ?? 'Not recorded'
}

function displayInputRole(value: string | null): string {
  if (!value) {
    return 'Reference'
  }

  return referenceRoleLabels[value as GenerationInputRole] ?? titleCase(value)
}

function defaultCampaignBrandRole(assetType: BrandAssetType): string {
  if (assetType === 'logo') {
    return 'primary_logo'
  }

  if (assetType === 'product_image') {
    return 'product'
  }

  if (assetType === 'style_reference') {
    return 'style_reference'
  }

  return 'brand_reference'
}

function isBrandAssetPreviewable(asset: BrandAssetDto): boolean {
  return asset.content_type.startsWith('image/')
}

function formatVerifiedState(value: boolean | null): string {
  if (value === true) {
    return 'Verified'
  }

  if (value === false) {
    return 'Not verified'
  }

  return 'Not recorded'
}

function isImageDescriptor(
  contentType: string | null,
  filename: string | null,
  url: string | null = null,
): boolean {
  if (contentType?.startsWith('image/')) {
    return true
  }

  return /\.(avif|gif|jpe?g|png|webp)$/i.test(filename ?? url ?? '')
}

function getGeneratedPreview(
  metadata: Record<string, unknown>,
): GeneratedPreview | null {
  const provenance = isRecord(metadata.provenance) ? metadata.provenance : null
  const assetCandidates = [
    ...readAssetMetadataList(metadata.assets),
    ...readAssetMetadataList(provenance?.assets),
  ]
    .map((asset) => ({
      url: readString(asset.url),
      storageKey: readString(asset.storage_key),
      contentType: readString(asset.content_type),
      filename: readString(asset.filename),
    }))
    .filter((asset) => asset.url || asset.storageKey)

  return (
    assetCandidates.find((asset) =>
      isImageDescriptor(asset.contentType, asset.filename, asset.url),
    ) ??
    assetCandidates[0] ??
    null
  )
}

function getInputAssetsFromMetadata(
  metadata: Record<string, unknown>,
): VersionInputAsset[] {
  const provenance = isRecord(metadata.provenance) ? metadata.provenance : null
  const inputCandidates = [
    ...readAssetMetadataList(metadata.input_assets),
    ...readAssetMetadataList(provenance?.input_assets),
  ]

  return mergeVersionInputAssets(
    inputCandidates
      .map((inputAsset) => ({
        id: null,
        role: readString(inputAsset.role),
        storageKey: readString(inputAsset.storage_key),
        filename: readString(inputAsset.filename),
        contentType: readString(inputAsset.content_type),
        sizeBytes: readNumber(inputAsset.size_bytes),
        sha256: readString(inputAsset.sha256),
        source: readString(inputAsset.source),
        brandAssetId: readString(inputAsset.brand_asset_id),
        brandAssetName: readString(inputAsset.brand_asset_name),
        usageGuidance: readString(inputAsset.usage_guidance),
      }))
      .filter((inputAsset) => inputAsset.storageKey || inputAsset.filename),
  )
}

function getVersionInputFingerprint(inputAsset: VersionInputAsset): string {
  if (inputAsset.storageKey) {
    return `storage:${inputAsset.storageKey}`
  }

  return `name:${inputAsset.role ?? 'reference'}:${inputAsset.filename ?? 'input'}`
}

function mergeVersionInputAssets(
  ...inputAssetGroups: VersionInputAsset[][]
): VersionInputAsset[] {
  const mergedInputAssets: VersionInputAsset[] = []
  const inputAssetIndexes = new Map<string, number>()

  for (const inputAssetGroup of inputAssetGroups) {
    for (const inputAsset of inputAssetGroup) {
      const fingerprint = getVersionInputFingerprint(inputAsset)
      const existingIndex = inputAssetIndexes.get(fingerprint)

      if (existingIndex === undefined) {
        inputAssetIndexes.set(fingerprint, mergedInputAssets.length)
        mergedInputAssets.push(inputAsset)
        continue
      }

      if (!mergedInputAssets[existingIndex].id && inputAsset.id) {
        mergedInputAssets[existingIndex] = {
          ...inputAsset,
          source: mergedInputAssets[existingIndex].source ?? inputAsset.source,
        }
      }
    }
  }

  return mergedInputAssets
}

function hasImageArtifact(version: AssetVersion): boolean {
  const generatedPreview = version.generatedPreview

  if (
    isImageDescriptor(
      version.artifactContentType,
      version.artifactFilename,
      null,
    )
  ) {
    return true
  }

  if (
    generatedPreview &&
    isImageDescriptor(
      generatedPreview.contentType,
      generatedPreview.filename,
      generatedPreview.url,
    )
  ) {
    return true
  }

  return Boolean(version.artifactStorageKey && generatedPreview)
}

function getAssetCardPreviewVersion(asset: Asset): AssetVersion | null {
  return asset.versions.reduce<AssetVersion | null>((latestVersion, version) => {
    if (!hasImageArtifact(version)) {
      return latestVersion
    }

    if (!latestVersion || version.versionNumber > latestVersion.versionNumber) {
      return version
    }

    return latestVersion
  }, null)
}

function sortVersionsNewestFirst(versions: AssetVersion[]): AssetVersion[] {
  return [...versions].sort(
    (firstVersion, secondVersion) =>
      secondVersion.versionNumber - firstVersion.versionNumber,
  )
}

function getImagePreviewUrl(
  version: AssetVersion,
  previewUrls: Record<string, ArtifactPreviewUrl>,
): string | null {
  const generatedPreviewUrl = version.generatedPreview?.url ?? null
  const previewUrl = previewUrls[version.versionId]

  if (
    version.artifactStorageKey &&
    previewUrl?.storageKey === version.artifactStorageKey
  ) {
    return previewUrl.url
  }

  return generatedPreviewUrl
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

function splitCommaList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function canCreateCampaign(form: CampaignFormState): boolean {
  return Boolean(
    form.name.trim() &&
      form.product.trim() &&
      form.audience.trim() &&
      form.owner.trim() &&
      form.goal.trim() &&
      form.tone.trim() &&
      form.brief.trim() &&
      splitCommaList(form.channels).length > 0,
  )
}

function makeReferenceImageId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function validateReferenceFile(file: File): string | null {
  if (!allowedReferenceImageTypes.includes(file.type)) {
    return `${file.name} must be a PNG, JPEG, or WebP image.`
  }

  if (file.size === 0) {
    return `${file.name} must not be empty.`
  }

  if (file.size > maxReferenceImageSizeBytes) {
    return `${file.name} must be ${formatFileSize(maxReferenceImageSizeBytes)} or smaller.`
  }

  return null
}

function createReferenceImage(file: File): ReferenceImage {
  return {
    id: makeReferenceImageId(),
    file,
    role: 'style_reference',
    previewUrl: URL.createObjectURL(file),
  }
}

function revokeReferenceImages(images: ReferenceImage[]): void {
  for (const image of images) {
    URL.revokeObjectURL(image.previewUrl)
  }
}

function referenceImagesToGenerationInputs(
  images: ReferenceImage[],
): GenerationInputFile[] {
  return images.map((image) => ({
    file: image.file,
    role: image.role,
  }))
}

function buildRefinePrompt(asset: Asset): string {
  return `Refine "${asset.title}" for ${asset.channel}. Keep the strongest idea, improve clarity, and make the next version more production-ready.`
}

function getNextVersionNumber(asset: Asset): number {
  const latestVersionNumber = asset.versions.reduce(
    (latest, version) => Math.max(latest, version.versionNumber),
    0,
  )

  return latestVersionNumber + 1
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

function mapAssetVersionInput(input: AssetVersionInputDto): VersionInputAsset {
  return {
    id: input.id,
    role: input.role,
    storageKey: input.storage_key,
    filename: input.filename,
    contentType: input.content_type,
    sizeBytes: input.size_bytes,
    sha256: input.sha256,
    source: input.source,
    brandAssetId: input.brand_asset_id,
    brandAssetName: input.brand_asset_name,
    usageGuidance: input.usage_guidance,
  }
}

function mapAssetVersion(version: AssetVersionDto): AssetVersion {
  const generatedPreview = getGeneratedPreview(version.generation_metadata)
  const metadataInputAssets = getInputAssetsFromMetadata(
    version.generation_metadata,
  )
  const databaseInputAssets = (version.inputs ?? []).map(mapAssetVersionInput)
  const inputAssets = mergeVersionInputAssets(
    metadataInputAssets,
    databaseInputAssets,
  )

  return {
    id: `v${version.version_number}`,
    versionId: version.id,
    versionNumber: version.version_number,
    created: version.provider,
    label: version.label,
    prompt: version.prompt,
    model: version.model,
    provider: version.provider,
    storageKey: version.storage_key,
    artifactStorageKey: version.artifact_storage_key,
    artifactFilename: version.artifact_filename,
    artifactContentType: version.artifact_content_type,
    artifactSizeBytes: version.artifact_size_bytes,
    generationMetadata: version.generation_metadata,
    generatedPreview,
    inputAssets,
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
  const [isCreateCampaignOpen, setIsCreateCampaignOpen] = useState(false)
  const [isBrandAssetModalOpen, setIsBrandAssetModalOpen] = useState(false)
  const [isCreatingCampaign, setIsCreatingCampaign] = useState(false)
  const [isLoadingBrandAssets, setIsLoadingBrandAssets] = useState(false)
  const [isUploadingBrandAsset, setIsUploadingBrandAsset] = useState(false)
  const [isGenerating, setIsGenerating] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [isSavingStatus, setIsSavingStatus] = useState(false)
  const [isRefining, setIsRefining] = useState(false)
  const [deletingCampaignId, setDeletingCampaignId] = useState<string | null>(
    null,
  )
  const [openCampaignMenuId, setOpenCampaignMenuId] = useState<string | null>(
    null,
  )
  const [campaignForm, setCampaignForm] =
    useState<CampaignFormState>(defaultCampaignForm)
  const [brandAssetForm, setBrandAssetForm] = useState<BrandAssetFormState>(
    defaultBrandAssetForm,
  )
  const [brandAssetFile, setBrandAssetFile] = useState<File | null>(null)
  const [attachUploadedBrandAsset, setAttachUploadedBrandAsset] = useState(true)
  const [uploadedBrandAssetRole, setUploadedBrandAssetRole] =
    useState('primary_logo')
  const [brandAssets, setBrandAssets] = useState<BrandAssetDto[]>([])
  const [campaignBrandAssets, setCampaignBrandAssets] = useState<
    CampaignBrandAssetDto[]
  >([])
  const [brandAssetRoles, setBrandAssetRoles] = useState<Record<string, string>>(
    {},
  )
  const [brandAssetPreviewUrls, setBrandAssetPreviewUrls] = useState<
    Record<string, string>
  >({})
  const [attachingBrandAssetId, setAttachingBrandAssetId] = useState<
    string | null
  >(null)
  const [detachingBrandAssetLinkId, setDetachingBrandAssetLinkId] = useState<
    string | null
  >(null)
  const [openingVersionId, setOpeningVersionId] = useState<string | null>(null)
  const [openingArtifactVersionId, setOpeningArtifactVersionId] = useState<
    string | null
  >(null)
  const [uploadingArtifactVersionId, setUploadingArtifactVersionId] = useState<
    string | null
  >(null)
  const [artifactPreviewUrls, setArtifactPreviewUrls] = useState<
    Record<string, ArtifactPreviewUrl>
  >({})
  const [artifactPreviewLoadingIds, setArtifactPreviewLoadingIds] = useState<
    Record<string, boolean>
  >({})
  const [artifactPreviewErrors, setArtifactPreviewErrors] = useState<
    Record<string, string>
  >({})
  const [openProvenanceVersionIds, setOpenProvenanceVersionIds] = useState<
    Record<string, boolean>
  >({})
  const [refinePrompts, setRefinePrompts] = useState<Record<string, string>>({})
  const [generationReferenceImages, setGenerationReferenceImages] = useState<
    ReferenceImage[]
  >([])
  const [refineReferenceImages, setRefineReferenceImages] = useState<
    Record<string, ReferenceImage[]>
  >({})
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const generationReferenceImagesRef = useRef<ReferenceImage[]>([])
  const refineReferenceImagesRef = useRef<Record<string, ReferenceImage[]>>({})
  const brandAssetFileInputRef = useRef<HTMLInputElement>(null)

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

  useEffect(() => {
    generationReferenceImagesRef.current = generationReferenceImages
  }, [generationReferenceImages])

  useEffect(() => {
    refineReferenceImagesRef.current = refineReferenceImages
  }, [refineReferenceImages])

  useEffect(
    () => () => {
      revokeReferenceImages(generationReferenceImagesRef.current)
      for (const images of Object.values(refineReferenceImagesRef.current)) {
        revokeReferenceImages(images)
      }
    },
    [],
  )

  const selectedCampaign = useMemo(
    () =>
      campaigns.find((campaign) => campaign.id === selectedCampaignId) ?? null,
    [campaigns, selectedCampaignId],
  )

  useEffect(() => {
    if (!isCreateCampaignOpen) {
      return
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setCampaignForm(defaultCampaignForm)
        setIsCreateCampaignOpen(false)
      }
    }

    window.addEventListener('keydown', closeOnEscape)

    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [isCreateCampaignOpen])

  useEffect(() => {
    if (!isBrandAssetModalOpen) {
      return
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setIsBrandAssetModalOpen(false)
      }
    }

    window.addEventListener('keydown', closeOnEscape)

    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [isBrandAssetModalOpen])

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

  useEffect(() => {
    let isCancelled = false

    async function loadBrandAssets(campaignId: string) {
      setIsLoadingBrandAssets(true)

      try {
        const [library, attachments] = await Promise.all([
          fetchBrandAssets({ isActive: true, limit: 200 }),
          fetchCampaignBrandAssets(campaignId),
        ])

        if (isCancelled) {
          return
        }

        setBrandAssets(library)
        setCampaignBrandAssets(attachments)
        setBrandAssetRoles((currentRoles) => {
          const nextRoles = { ...currentRoles }
          for (const asset of library) {
            nextRoles[asset.id] ??= defaultCampaignBrandRole(asset.asset_type)
          }
          return nextRoles
        })
      } catch (error) {
        if (!isCancelled) {
          setBrandAssets([])
          setCampaignBrandAssets([])
          setErrorMessage(getErrorMessage(error))
        }
      } finally {
        if (!isCancelled) {
          setIsLoadingBrandAssets(false)
        }
      }
    }

    if (!selectedCampaignId) {
      setBrandAssets([])
      setCampaignBrandAssets([])
      return
    }

    void loadBrandAssets(selectedCampaignId)

    return () => {
      isCancelled = true
    }
  }, [selectedCampaignId])

  useEffect(() => {
    let isCancelled = false
    const assetsById = new Map<string, BrandAssetDto>()

    for (const asset of brandAssets) {
      assetsById.set(asset.id, asset)
    }
    for (const attachment of campaignBrandAssets) {
      assetsById.set(attachment.brand_asset.id, attachment.brand_asset)
    }

    const previewableAssets = [...assetsById.values()].filter(
      isBrandAssetPreviewable,
    )

    async function loadPreviewUrls() {
      const previewResults = await Promise.allSettled(
        previewableAssets.map(async (asset) => ({
          assetId: asset.id,
          download: await fetchBrandAssetDownloadUrl(asset.id),
        })),
      )

      if (isCancelled) {
        return
      }

      setBrandAssetPreviewUrls((currentUrls) => {
        const nextUrls = { ...currentUrls }
        for (const result of previewResults) {
          if (result.status === 'fulfilled') {
            nextUrls[result.value.assetId] = result.value.download.download_url
          }
        }
        return nextUrls
      })
    }

    if (previewableAssets.length > 0) {
      void loadPreviewUrls()
    }

    return () => {
      isCancelled = true
    }
  }, [brandAssets, campaignBrandAssets])

  const campaignAssets = assets

  const channels = useMemo(
    () => ['All', ...(selectedCampaign?.channels ?? [])],
    [selectedCampaign],
  )

  const filteredAssets = useMemo(
    () =>
      campaignAssets.filter((asset) => {
        const matchesStatus =
          statusFilter === 'all' ? true : asset.status === statusFilter
        const matchesChannel =
          channelFilter === 'All' ? true : asset.channel === channelFilter

        return matchesStatus && matchesChannel
      }),
    [campaignAssets, channelFilter, statusFilter],
  )

  const selectedAsset =
    filteredAssets.find((asset) => asset.id === selectedAssetId) ??
    filteredAssets[0] ??
    null

  const selectedVersions = useMemo(
    () => (selectedAsset ? sortVersionsNewestFirst(selectedAsset.versions) : []),
    [selectedAsset],
  )
  const latestSelectedVersion = selectedVersions[0] ?? null
  const previousSelectedVersions = selectedVersions.slice(1)
  const selectedRefineReferenceImages = selectedAsset
    ? (refineReferenceImages[selectedAsset.id] ?? [])
    : []

  const refinePrompt = selectedAsset
    ? (refinePrompts[selectedAsset.id] ?? buildRefinePrompt(selectedAsset))
    : ''

  const approvedCount = campaignAssets.filter(
    (asset) => asset.status === 'approved',
  ).length

  useEffect(() => {
    let isCancelled = false

    async function loadArtifactPreviews(asset: Asset) {
      const imageVersions = asset.versions.filter(hasImageArtifact)

      await Promise.all(
        imageVersions.map(async (version) => {
          if (!version.artifactStorageKey) {
            return
          }

          setArtifactPreviewLoadingIds((currentLoadingIds) => ({
            ...currentLoadingIds,
            [version.versionId]: true,
          }))

          try {
            const download = await fetchAssetVersionArtifactDownloadUrl(
              asset.id,
              version.versionId,
            )

            if (isCancelled) {
              return
            }

            setArtifactPreviewUrls((currentPreviewUrls) => ({
              ...currentPreviewUrls,
              [version.versionId]: {
                storageKey: version.artifactStorageKey ?? '',
                url: download.download_url,
              },
            }))
            setArtifactPreviewErrors((currentPreviewErrors) => {
              const nextPreviewErrors = { ...currentPreviewErrors }
              delete nextPreviewErrors[version.versionId]
              return nextPreviewErrors
            })
          } catch (error) {
            if (!isCancelled) {
              setArtifactPreviewErrors((currentPreviewErrors) => ({
                ...currentPreviewErrors,
                [version.versionId]: getErrorMessage(error),
              }))
            }
          } finally {
            if (!isCancelled) {
              setArtifactPreviewLoadingIds((currentLoadingIds) => {
                const nextLoadingIds = { ...currentLoadingIds }
                delete nextLoadingIds[version.versionId]
                return nextLoadingIds
              })
            }
          }
        }),
      )
    }

    if (selectedAsset) {
      void loadArtifactPreviews(selectedAsset)
    }

    return () => {
      isCancelled = true
    }
  }, [selectedAsset])

  useEffect(() => {
    let isCancelled = false

    async function loadAssetCardPreviews() {
      const previewTargets: Array<{
        assetId: string
        version: AssetVersion
      }> = []

      for (const asset of filteredAssets) {
        const version = getAssetCardPreviewVersion(asset)

        if (version?.artifactStorageKey) {
          previewTargets.push({
            assetId: asset.id,
            version,
          })
        }
      }

      await Promise.all(
        previewTargets.map(async ({ assetId, version }) => {
          setArtifactPreviewLoadingIds((currentLoadingIds) => ({
            ...currentLoadingIds,
            [version.versionId]: true,
          }))

          try {
            const download = await fetchAssetVersionArtifactDownloadUrl(
              assetId,
              version.versionId,
            )

            if (isCancelled) {
              return
            }

            setArtifactPreviewUrls((currentPreviewUrls) => ({
              ...currentPreviewUrls,
              [version.versionId]: {
                storageKey: version.artifactStorageKey ?? '',
                url: download.download_url,
              },
            }))
            setArtifactPreviewErrors((currentPreviewErrors) => {
              const nextPreviewErrors = { ...currentPreviewErrors }
              delete nextPreviewErrors[version.versionId]
              return nextPreviewErrors
            })
          } catch (error) {
            if (!isCancelled) {
              setArtifactPreviewErrors((currentPreviewErrors) => ({
                ...currentPreviewErrors,
                [version.versionId]: getErrorMessage(error),
              }))
            }
          } finally {
            if (!isCancelled) {
              setArtifactPreviewLoadingIds((currentLoadingIds) => {
                const nextLoadingIds = { ...currentLoadingIds }
                delete nextLoadingIds[version.versionId]
                return nextLoadingIds
              })
            }
          }
        }),
      )
    }

    void loadAssetCardPreviews()

    return () => {
      isCancelled = true
    }
  }, [filteredAssets])

  function createReferenceImagesFromFiles(
    files: FileList | null,
    currentCount: number,
  ): ReferenceImage[] {
    if (!files?.length) {
      return []
    }

    const nextFiles = Array.from(files)

    if (currentCount + nextFiles.length > maxReferenceImageCount) {
      setErrorMessage(
        `Reference images are limited to ${maxReferenceImageCount} files.`,
      )
      return []
    }

    for (const file of nextFiles) {
      const validationError = validateReferenceFile(file)

      if (validationError) {
        setErrorMessage(validationError)
        return []
      }
    }

    setErrorMessage(null)
    return nextFiles.map(createReferenceImage)
  }

  function addGenerationReferenceImages(files: FileList | null) {
    const nextImages = createReferenceImagesFromFiles(
      files,
      generationReferenceImages.length,
    )

    if (nextImages.length) {
      setGenerationReferenceImages((currentImages) => [
        ...currentImages,
        ...nextImages,
      ])
    }
  }

  function addRefineReferenceImages(assetId: string, files: FileList | null) {
    const currentImages = refineReferenceImages[assetId] ?? []
    const nextImages = createReferenceImagesFromFiles(
      files,
      currentImages.length,
    )

    if (nextImages.length) {
      setRefineReferenceImages((currentImageMap) => ({
        ...currentImageMap,
        [assetId]: [...(currentImageMap[assetId] ?? []), ...nextImages],
      }))
    }
  }

  function updateGenerationReferenceRole(
    imageId: string,
    role: GenerationInputRole,
  ) {
    setGenerationReferenceImages((currentImages) =>
      currentImages.map((image) =>
        image.id === imageId ? { ...image, role } : image,
      ),
    )
  }

  function updateRefineReferenceRole(
    assetId: string,
    imageId: string,
    role: GenerationInputRole,
  ) {
    setRefineReferenceImages((currentImageMap) => ({
      ...currentImageMap,
      [assetId]: (currentImageMap[assetId] ?? []).map((image) =>
        image.id === imageId ? { ...image, role } : image,
      ),
    }))
  }

  function removeGenerationReferenceImage(imageId: string) {
    setGenerationReferenceImages((currentImages) => {
      const removedImages = currentImages.filter((image) => image.id === imageId)
      revokeReferenceImages(removedImages)
      return currentImages.filter((image) => image.id !== imageId)
    })
  }

  function removeRefineReferenceImage(assetId: string, imageId: string) {
    setRefineReferenceImages((currentImageMap) => {
      const currentImages = currentImageMap[assetId] ?? []
      const removedImages = currentImages.filter((image) => image.id === imageId)
      const nextImages = currentImages.filter((image) => image.id !== imageId)
      const nextImageMap = { ...currentImageMap }

      revokeReferenceImages(removedImages)

      if (nextImages.length) {
        nextImageMap[assetId] = nextImages
      } else {
        delete nextImageMap[assetId]
      }

      return nextImageMap
    })
  }

  function clearGenerationReferenceImages() {
    setGenerationReferenceImages((currentImages) => {
      revokeReferenceImages(currentImages)
      return []
    })
  }

  function clearRefineReferenceImages(assetId: string) {
    setRefineReferenceImages((currentImageMap) => {
      const currentImages = currentImageMap[assetId] ?? []
      const nextImageMap = { ...currentImageMap }

      revokeReferenceImages(currentImages)
      delete nextImageMap[assetId]

      return nextImageMap
    })
  }

  function renderReferenceImagePicker({
    images,
    inputId,
    disabled,
    onAdd,
    onRemove,
    onRoleChange,
  }: {
    images: ReferenceImage[]
    inputId: string
    disabled: boolean
    onAdd: (files: FileList | null) => void
    onRemove: (imageId: string) => void
    onRoleChange: (imageId: string, role: GenerationInputRole) => void
  }) {
    return (
      <div className="reference-inputs">
        <div className="reference-inputs-heading">
          <span>Reference images</span>
          <label
            className={`metadata-button artifact-upload reference-add-button ${
              disabled || images.length >= maxReferenceImageCount
                ? 'is-disabled'
                : ''
            }`}
            htmlFor={inputId}
          >
            Add images
            <input
              accept={allowedReferenceImageTypes.join(',')}
              disabled={disabled || images.length >= maxReferenceImageCount}
              id={inputId}
              multiple
              onChange={(event) => {
                onAdd(event.currentTarget.files)
                event.currentTarget.value = ''
              }}
              type="file"
            />
          </label>
        </div>

        {images.length > 0 && (
          <div className="reference-image-list">
            {images.map((image) => (
              <div className="reference-image-card" key={image.id}>
                <img alt={`${image.file.name} reference`} src={image.previewUrl} />
                <div className="reference-image-meta">
                  <strong>{image.file.name}</strong>
                  <small>{formatFileSize(image.file.size)}</small>
                  <select
                    aria-label={`Role for ${image.file.name}`}
                    disabled={disabled}
                    onChange={(event) =>
                      onRoleChange(
                        image.id,
                        event.target.value as GenerationInputRole,
                      )
                    }
                    value={image.role}
                  >
                    {referenceRoleOptions.map((role) => (
                      <option key={role} value={role}>
                        {referenceRoleLabels[role]}
                      </option>
                    ))}
                  </select>
                </div>
                <button
                  aria-label={`Remove ${image.file.name}`}
                  className="reference-remove-button"
                  disabled={disabled}
                  onClick={() => onRemove(image.id)}
                  type="button"
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  function selectCampaign(campaignId: string) {
    if (campaignId === selectedCampaignId) {
      setOpenCampaignMenuId(null)
      return
    }

    const nextCampaign = campaigns.find((campaign) => campaign.id === campaignId)

    setOpenCampaignMenuId(null)
    setIsCreateCampaignOpen(false)
    setIsBrandAssetModalOpen(false)
    setSelectedCampaignId(campaignId)
    setSelectedAssetId('')
    setAssets([])
    setStatusFilter('all')
    setChannelFilter('All')
    setRequestChannel(nextCampaign?.channels[0] ?? '')
    clearGenerationReferenceImages()
  }

  function resetBrandAssetForm() {
    setBrandAssetForm(defaultBrandAssetForm)
    setBrandAssetFile(null)
    setAttachUploadedBrandAsset(true)
    setUploadedBrandAssetRole('primary_logo')
    if (brandAssetFileInputRef.current) {
      brandAssetFileInputRef.current.value = ''
    }
  }

  function closeBrandAssetModal() {
    setIsBrandAssetModalOpen(false)
    resetBrandAssetForm()
  }

  async function uploadBrandAssetFromForm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (!selectedCampaign || !brandAssetFile || !brandAssetForm.name.trim()) {
      return
    }

    if (brandAssetFile.size === 0) {
      setErrorMessage('Brand asset file must not be empty')
      return
    }

    if (brandAssetFile.size > maxReferenceImageSizeBytes) {
      setErrorMessage('Brand asset file must be 25 MB or smaller')
      return
    }

    setIsUploadingBrandAsset(true)
    setErrorMessage(null)

    try {
      const uploadedAsset = await uploadBrandAsset(
        {
          name: brandAssetForm.name.trim(),
          asset_type: brandAssetForm.assetType,
          description: brandAssetForm.description.trim() || null,
          usage_guidance: brandAssetForm.usageGuidance.trim() || null,
          tags: splitCommaList(brandAssetForm.tags),
          source_url: brandAssetForm.sourceUrl.trim() || null,
        },
        brandAssetFile,
      )

      setBrandAssets((currentAssets) => [
        uploadedAsset,
        ...currentAssets.filter((asset) => asset.id !== uploadedAsset.id),
      ])
      setBrandAssetRoles((currentRoles) => ({
        ...currentRoles,
        [uploadedAsset.id]: defaultCampaignBrandRole(uploadedAsset.asset_type),
      }))

      if (attachUploadedBrandAsset) {
        const attachment = await attachBrandAssetToCampaign(
          selectedCampaign.id,
          {
            brand_asset_id: uploadedAsset.id,
            role: uploadedBrandAssetRole,
          },
        )
        setCampaignBrandAssets((currentAttachments) => [
          attachment,
          ...currentAttachments,
        ])
      }

      closeBrandAssetModal()
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setIsUploadingBrandAsset(false)
    }
  }

  async function attachLibraryBrandAsset(asset: BrandAssetDto) {
    if (!selectedCampaign) {
      return
    }

    const role =
      brandAssetRoles[asset.id] ?? defaultCampaignBrandRole(asset.asset_type)
    setAttachingBrandAssetId(asset.id)
    setErrorMessage(null)

    try {
      const attachment = await attachBrandAssetToCampaign(selectedCampaign.id, {
        brand_asset_id: asset.id,
        role,
      })
      setCampaignBrandAssets((currentAttachments) => [
        attachment,
        ...currentAttachments,
      ])
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setAttachingBrandAssetId(null)
    }
  }

  async function detachCampaignBrandAsset(attachment: CampaignBrandAssetDto) {
    if (!selectedCampaign) {
      return
    }

    setDetachingBrandAssetLinkId(attachment.id)
    setErrorMessage(null)

    try {
      await detachBrandAssetFromCampaign(selectedCampaign.id, attachment.id)
      setCampaignBrandAssets((currentAttachments) =>
        currentAttachments.filter((item) => item.id !== attachment.id),
      )
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setDetachingBrandAssetLinkId(null)
    }
  }

  async function openBrandAsset(asset: BrandAssetDto) {
    setErrorMessage(null)

    try {
      const previewUrl = brandAssetPreviewUrls[asset.id]
      const downloadUrl = previewUrl
        ? previewUrl
        : (await fetchBrandAssetDownloadUrl(asset.id)).download_url
      const openedWindow = window.open(downloadUrl, '_blank', 'noopener,noreferrer')

      if (!openedWindow) {
        setErrorMessage('The browser blocked the brand asset tab')
      }
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    }
  }

  async function createCampaignFromForm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (!canCreateCampaign(campaignForm)) {
      return
    }

    setIsCreatingCampaign(true)
    setErrorMessage(null)

    try {
      const createdCampaign = mapCampaign(
        await createCampaign({
          name: campaignForm.name.trim(),
          product: campaignForm.product.trim(),
          audience: campaignForm.audience.trim(),
          status: campaignForm.status.trim() || 'drafting',
          due_date: campaignForm.dueDate || null,
          owner: campaignForm.owner.trim(),
          goal: campaignForm.goal.trim(),
          tone: campaignForm.tone.trim(),
          brief: campaignForm.brief.trim(),
          channels: splitCommaList(campaignForm.channels),
          brand_inputs: splitCommaList(campaignForm.brandInputs),
        }),
        0,
      )

      setCampaigns((currentCampaigns) => [
        createdCampaign,
        ...currentCampaigns.filter(
          (campaign) => campaign.id !== createdCampaign.id,
        ),
      ])
      setSelectedCampaignId(createdCampaign.id)
      setSelectedAssetId('')
      setAssets([])
      setStatusFilter('all')
      setChannelFilter('All')
      setRequestChannel(createdCampaign.channels[0] ?? '')
      clearGenerationReferenceImages()
      setCampaignForm(defaultCampaignForm)
      setIsCreateCampaignOpen(false)
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setIsCreatingCampaign(false)
    }
  }

  async function deleteCampaignFromMenu(campaign: Campaign) {
    const shouldDelete = window.confirm(
      `Delete "${campaign.name}" and its database assets?`,
    )

    if (!shouldDelete) {
      return
    }

    setDeletingCampaignId(campaign.id)
    setErrorMessage(null)

    try {
      await deleteCampaign(campaign.id)

      const nextCampaigns = campaigns.filter(
        (currentCampaign) => currentCampaign.id !== campaign.id,
      )
      const nextSelectedCampaign =
        selectedCampaignId === campaign.id
          ? (nextCampaigns[0] ?? null)
          : selectedCampaign

      setCampaigns(nextCampaigns)
      setOpenCampaignMenuId(null)

      if (selectedCampaignId === campaign.id) {
        setSelectedCampaignId(nextSelectedCampaign?.id ?? '')
        setSelectedAssetId('')
        setAssets([])
        setStatusFilter('all')
        setChannelFilter('All')
        setRequestChannel(nextSelectedCampaign?.channels[0] ?? '')
        clearGenerationReferenceImages()
      }
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setDeletingCampaignId(null)
    }
  }

  async function downloadCampaignExport() {
    if (!selectedCampaign) {
      return
    }

    setIsExporting(true)
    setErrorMessage(null)

    try {
      const download = await exportCampaignPack(selectedCampaign.id)
      const url = URL.createObjectURL(download.blob)
      const link = document.createElement('a')

      link.href = url
      link.download = download.filename
      link.style.display = 'none'
      document.body.append(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 0)
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setIsExporting(false)
    }
  }

  async function refreshAsset(assetId: string): Promise<Asset> {
    const refreshedAsset = mapAsset(await fetchAsset(assetId))

    setAssets((currentAssets) =>
      currentAssets.map((asset) =>
        asset.id === refreshedAsset.id ? refreshedAsset : asset,
      ),
    )
    setSelectedAssetId(refreshedAsset.id)

    return refreshedAsset
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

    const formatValue = formatValues[requestFormat]

    setIsGenerating(true)
    setErrorMessage(null)

    try {
      const assetPayload: AssetGenerationCreateDto = {
        title: `${requestChannel} ${requestFormat.toLowerCase()} draft`,
        format: formatValue,
        channel: requestChannel,
        prompt: requestPrompt,
        status: 'draft',
        reviewer: null,
        tags: ['generated', requestChannel.toLowerCase().replace(/\s/g, '-')],
        summary:
          'A Genblaze-generated creative direction with durable B2 storage and provenance metadata.',
        generation_parameters: {
          campaign_name: selectedCampaign.name,
          product: selectedCampaign.product,
          audience: selectedCampaign.audience,
          format: formatValue,
          channel: requestChannel,
        },
      }
      const createdAssetDto = generationReferenceImages.length
        ? await generateCampaignAssetWithInputs(
            selectedCampaign.id,
            assetPayload,
            referenceImagesToGenerationInputs(generationReferenceImages),
          )
        : await generateCampaignAsset(selectedCampaign.id, assetPayload)
      const createdAsset = mapAsset(
        createdAssetDto,
      )
      setAssets((currentAssets) => [
        createdAsset,
        ...currentAssets.filter((asset) => asset.id !== createdAsset.id),
      ])
      setSelectedAssetId(createdAsset.id)
      setStatusFilter('all')
      setChannelFilter('All')
      clearGenerationReferenceImages()
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setIsGenerating(false)
    }
  }

  async function refineAsset() {
    if (!selectedAsset) {
      return
    }

    const trimmedPrompt = refinePrompt.trim()

    if (!trimmedPrompt) {
      return
    }

    const versionNumber = getNextVersionNumber(selectedAsset)

    setIsRefining(true)
    setErrorMessage(null)

    try {
      const referenceImages = refineReferenceImages[selectedAsset.id] ?? []
      const versionPayload: AssetVersionGenerationCreateDto = {
        prompt: trimmedPrompt,
        label: `Genblaze refinement ${versionNumber}`,
        generation_parameters: {
          asset_title: selectedAsset.title,
          format: formatValues[selectedAsset.format],
          channel: selectedAsset.channel,
        },
      }
      const refreshedAssetDto = referenceImages.length
        ? await generateAssetVersionWithInputs(
            selectedAsset.id,
            versionPayload,
            referenceImagesToGenerationInputs(referenceImages),
          )
        : await generateAssetVersion(selectedAsset.id, versionPayload)
      const refreshedAsset = mapAsset(
        refreshedAssetDto,
      )
      setAssets((currentAssets) =>
        currentAssets.map((asset) =>
          asset.id === refreshedAsset.id ? refreshedAsset : asset,
        ),
      )
      setSelectedAssetId(refreshedAsset.id)
      setRefinePrompts((currentPrompts) => {
        const nextPrompts = { ...currentPrompts }
        delete nextPrompts[selectedAsset.id]
        return nextPrompts
      })
      clearRefineReferenceImages(selectedAsset.id)
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setIsRefining(false)
    }
  }
  async function uploadArtifact(version: AssetVersion, file: File | null) {
    if (!selectedAsset || !file) {
      return
    }

    setUploadingArtifactVersionId(version.versionId)
    setErrorMessage(null)
    setArtifactPreviewUrls((currentPreviewUrls) => {
      const nextPreviewUrls = { ...currentPreviewUrls }
      delete nextPreviewUrls[version.versionId]
      return nextPreviewUrls
    })
    setArtifactPreviewErrors((currentPreviewErrors) => {
      const nextPreviewErrors = { ...currentPreviewErrors }
      delete nextPreviewErrors[version.versionId]
      return nextPreviewErrors
    })

    try {
      await uploadAssetVersionArtifact(selectedAsset.id, version.versionId, file)
      await refreshAsset(selectedAsset.id)
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
    } finally {
      setUploadingArtifactVersionId(null)
    }
  }

  function openPendingTab(blockedMessage: string): Window | null {
    const openedWindow = window.open('about:blank', '_blank')

    if (!openedWindow) {
      setErrorMessage(blockedMessage)
      return null
    }

    openedWindow.opener = null
    openedWindow.document.title = 'Opening asset...'
    openedWindow.document.body.textContent = 'Preparing signed download URL...'

    return openedWindow
  }

  async function openArtifact(version: AssetVersion) {
    if (!selectedAsset || !version.artifactStorageKey) {
      return
    }

    const openedWindow = openPendingTab('The browser blocked the artifact tab')
    if (!openedWindow) {
      return
    }

    setOpeningArtifactVersionId(version.versionId)
    setErrorMessage(null)

    try {
      const download = await fetchAssetVersionArtifactDownloadUrl(
        selectedAsset.id,
        version.versionId,
      )
      openedWindow.location.assign(download.download_url)
    } catch (error) {
      openedWindow.close()
      setErrorMessage(getErrorMessage(error))
    } finally {
      setOpeningArtifactVersionId(null)
    }
  }

  function openGeneratedPreview(version: AssetVersion) {
    const previewUrl = version.generatedPreview?.url

    if (!previewUrl) {
      return
    }

    const openedWindow = window.open(previewUrl, '_blank', 'noopener,noreferrer')

    if (!openedWindow) {
      setErrorMessage('The browser blocked the generated preview tab')
    }
  }

  function openPreview(version: AssetVersion) {
    if (version.artifactStorageKey) {
      void openArtifact(version)
      return
    }

    openGeneratedPreview(version)
  }

  function renderArtifactPreview(version: AssetVersion) {
    const generatedPreviewUrl = version.generatedPreview?.url ?? null

    if (!version.artifactStorageKey && !generatedPreviewUrl) {
      return (
        <span className="artifact-summary artifact-empty">
          No artifact attached
        </span>
      )
    }

    if (!hasImageArtifact(version)) {
      return (
        <button
          className="artifact-file-preview"
          onClick={() => openPreview(version)}
          type="button"
        >
          <span>{getFileExtension(getVersionFilename(version))}</span>
          <strong>{getVersionFilename(version) ?? 'Artifact'}</strong>
          <small>{formatArtifactDetails(version)}</small>
        </button>
      )
    }

    const previewUrl = artifactPreviewUrls[version.versionId]
    const imagePreviewUrl =
      previewUrl?.storageKey === version.artifactStorageKey
        ? previewUrl.url
        : generatedPreviewUrl

    if (imagePreviewUrl) {
      return (
        <button
          className="artifact-image-preview"
          onClick={() => openPreview(version)}
          type="button"
        >
          <img
            alt={getVersionFilename(version) ?? `${version.id} generated preview`}
            src={imagePreviewUrl}
          />
          <span className="artifact-image-caption">
            <strong>{getVersionFilename(version) ?? 'Generated preview'}</strong>
            <small>{formatArtifactDetails(version)}</small>
          </span>
        </button>
      )
    }

    if (artifactPreviewErrors[version.versionId]) {
      return (
        <span className="artifact-preview-state">Preview unavailable</span>
      )
    }

    return (
      <span className="artifact-preview-state">
        {artifactPreviewLoadingIds[version.versionId]
          ? 'Loading preview...'
          : 'Preview pending'}
      </span>
    )
  }

  function getVersionProvenance(version: AssetVersion) {
    const metadata = version.generationMetadata
    const provenance = isRecord(metadata.provenance) ? metadata.provenance : {}
    const artifactFlow = isRecord(metadata.artifact_flow)
      ? metadata.artifact_flow
      : isRecord(provenance.artifact_flow)
        ? provenance.artifact_flow
        : {}

    return {
      provider: firstString(metadata.provider, provenance.provider, version.provider),
      model: firstString(metadata.model, provenance.model, version.model),
      prompt: firstString(metadata.prompt, provenance.prompt, version.prompt),
      source: firstString(metadata.source, provenance.source),
      manifestUri: firstString(metadata.manifest_uri, provenance.manifest_uri),
      manifestHash: firstString(metadata.manifest_hash, provenance.manifest_hash),
      manifestVerified:
        readBoolean(metadata.manifest_verified) ??
        readBoolean(provenance.manifest_verified),
      generatedStorageKey: firstString(
        artifactFlow.source_storage_key,
        version.generatedPreview?.storageKey,
      ),
      artifactStorageKey: firstString(
        artifactFlow.storage_key,
        version.artifactStorageKey,
      ),
      sidecarStorageKey: version.storageKey,
      inputAssets: version.inputAssets.length
        ? version.inputAssets
        : getInputAssetsFromMetadata(metadata),
    }
  }

  function renderProvenanceDetails(version: AssetVersion) {
    const provenance = getVersionProvenance(version)
    const manifestLabel = provenance.manifestUri
      ? formatVerifiedState(provenance.manifestVerified)
      : 'Not recorded'

    return (
      <div className="provenance-panel" id={`provenance-${version.versionId}`}>
        <div className="provenance-grid">
          <div>
            <span>Provider</span>
            <strong>{displayValue(provenance.provider)}</strong>
          </div>
          <div>
            <span>Model</span>
            <strong>{displayValue(provenance.model)}</strong>
          </div>
          <div>
            <span>Manifest</span>
            <strong>{manifestLabel}</strong>
          </div>
        </div>

        <div className="provenance-item">
          <span>Prompt</span>
          <p>{displayValue(provenance.prompt)}</p>
        </div>

        <div className="provenance-item">
          <span>Input assets</span>
          {provenance.inputAssets.length > 0 ? (
            <div className="provenance-input-list">
              {provenance.inputAssets.map((inputAsset, index) => (
                <div
                  className="provenance-input-card"
                  key={
                    inputAsset.id ??
                    inputAsset.storageKey ??
                    `${inputAsset.role}-${inputAsset.filename}-${index}`
                  }
                >
                  <div className="provenance-input-head">
                    <strong>{displayInputRole(inputAsset.role)}</strong>
                    <span>
                      {[
                        inputAsset.contentType,
                        inputAsset.sizeBytes === null
                          ? null
                          : formatFileSize(inputAsset.sizeBytes),
                      ]
                        .filter(Boolean)
                        .join(' / ') || 'Stored input'}
                    </span>
                  </div>
                  <p>{displayValue(inputAsset.filename)}</p>
                  {inputAsset.brandAssetName && (
                    <p className="provenance-brand-name">
                      Brand asset: {inputAsset.brandAssetName}
                    </p>
                  )}
                  {inputAsset.usageGuidance && (
                    <small className="provenance-guidance">
                      {inputAsset.usageGuidance}
                    </small>
                  )}
                  <code>{displayValue(inputAsset.storageKey)}</code>
                  <div className="provenance-input-foot">
                    <span>{displayValue(inputAsset.source)}</span>
                    <span>{inputAsset.sha256?.slice(0, 12) ?? 'No hash'}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p>No reference images recorded.</p>
          )}
        </div>

        <div className="provenance-item">
          <span>Storage flow</span>
          <ol className="provenance-flow">
            <li>
              <span>Generated</span>
              <code>{displayValue(provenance.generatedStorageKey)}</code>
            </li>
            <li>
              <span>B2 artifact</span>
              <code>{displayValue(provenance.artifactStorageKey)}</code>
            </li>
            <li>
              <span>Sidecar</span>
              <code>{displayValue(provenance.sidecarStorageKey)}</code>
            </li>
          </ol>
        </div>

        <div className="provenance-item">
          <span>Manifest details</span>
          <div className="provenance-kv">
            <code>{displayValue(provenance.manifestUri)}</code>
            <code>{displayValue(provenance.manifestHash)}</code>
          </div>
        </div>

        <div className="provenance-foot">
          <span>{displayValue(provenance.source)}</span>
          <span>{version.artifactStorageKey ? 'Export ready' : 'Needs artifact'}</span>
        </div>
      </div>
    )
  }

  function toggleProvenance(versionId: string) {
    setOpenProvenanceVersionIds((currentVersionIds) => ({
      ...currentVersionIds,
      [versionId]: !currentVersionIds[versionId],
    }))
  }

  function renderProvenanceButton(version: AssetVersion) {
    const isOpen = Boolean(openProvenanceVersionIds[version.versionId])

    return (
      <button
        aria-controls={`provenance-${version.versionId}`}
        aria-expanded={isOpen}
        className="metadata-button"
        onClick={() => toggleProvenance(version.versionId)}
        type="button"
      >
        {isOpen ? 'Hide provenance' : 'Show provenance'}
      </button>
    )
  }

  async function openStoredMetadata(version: AssetVersion) {
    if (!selectedAsset) {
      return
    }

    const openedWindow = openPendingTab('The browser blocked the metadata tab')
    if (!openedWindow) {
      return
    }

    setOpeningVersionId(version.versionId)
    setErrorMessage(null)

    try {
      const download = await fetchAssetVersionDownloadUrl(
        selectedAsset.id,
        version.versionId,
      )
      openedWindow.location.assign(download.download_url)
    } catch (error) {
      openedWindow.close()
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
          <button
            className="button button-secondary"
            disabled={!selectedCampaign || isExporting}
            onClick={downloadCampaignExport}
            type="button"
          >
            {isExporting ? 'Exporting...' : 'Export pack'}
          </button>
        </div>
      </header>

      {errorMessage && (
        <div className="system-banner" role="alert">
          {errorMessage}
        </div>
      )}

      {isCreateCampaignOpen && (
        <div
          className="modal-backdrop"
          onMouseDown={() => {
            setCampaignForm(defaultCampaignForm)
            setIsCreateCampaignOpen(false)
          }}
        >
          <section
            aria-labelledby="campaign-modal-title"
            aria-modal="true"
            className="campaign-modal"
            onMouseDown={(event) => event.stopPropagation()}
            role="dialog"
          >
            <div className="campaign-modal-header">
              <div>
                <span className="eyebrow">Campaign</span>
                <h2 id="campaign-modal-title">New campaign</h2>
              </div>
              <button
                aria-label="Close campaign form"
                className="modal-close-button"
                onClick={() => {
                  setCampaignForm(defaultCampaignForm)
                  setIsCreateCampaignOpen(false)
                }}
                type="button"
              >
                x
              </button>
            </div>

            <form
              className="campaign-create-panel"
              onSubmit={createCampaignFromForm}
            >
              <div className="campaign-form-grid">
                <label className="field">
                  <span>Name</span>
                  <input
                    autoFocus
                    onChange={(event) =>
                      setCampaignForm((currentForm) => ({
                        ...currentForm,
                        name: event.target.value,
                      }))
                    }
                    required
                    value={campaignForm.name}
                  />
                </label>

                <label className="field">
                  <span>Product</span>
                  <input
                    onChange={(event) =>
                      setCampaignForm((currentForm) => ({
                        ...currentForm,
                        product: event.target.value,
                      }))
                    }
                    required
                    value={campaignForm.product}
                  />
                </label>
              </div>

              <label className="field">
                <span>Audience</span>
                <input
                  onChange={(event) =>
                    setCampaignForm((currentForm) => ({
                      ...currentForm,
                      audience: event.target.value,
                    }))
                  }
                  required
                  value={campaignForm.audience}
                />
              </label>

              <div className="campaign-form-grid">
                <label className="field">
                  <span>Owner</span>
                  <input
                    onChange={(event) =>
                      setCampaignForm((currentForm) => ({
                        ...currentForm,
                        owner: event.target.value,
                      }))
                    }
                    required
                    value={campaignForm.owner}
                  />
                </label>

                <label className="field">
                  <span>Due</span>
                  <input
                    onChange={(event) =>
                      setCampaignForm((currentForm) => ({
                        ...currentForm,
                        dueDate: event.target.value,
                      }))
                    }
                    type="date"
                    value={campaignForm.dueDate}
                  />
                </label>
              </div>

              <label className="field">
                <span>Goal</span>
                <textarea
                  onChange={(event) =>
                    setCampaignForm((currentForm) => ({
                      ...currentForm,
                      goal: event.target.value,
                    }))
                  }
                  required
                  rows={3}
                  value={campaignForm.goal}
                />
              </label>

              <label className="field">
                <span>Tone</span>
                <input
                  onChange={(event) =>
                    setCampaignForm((currentForm) => ({
                      ...currentForm,
                      tone: event.target.value,
                    }))
                  }
                  required
                  value={campaignForm.tone}
                />
              </label>

              <label className="field">
                <span>Brief</span>
                <textarea
                  onChange={(event) =>
                    setCampaignForm((currentForm) => ({
                      ...currentForm,
                      brief: event.target.value,
                    }))
                  }
                  required
                  rows={4}
                  value={campaignForm.brief}
                />
              </label>

              <div className="campaign-form-grid">
                <label className="field">
                  <span>Channels</span>
                  <input
                    onChange={(event) =>
                      setCampaignForm((currentForm) => ({
                        ...currentForm,
                        channels: event.target.value,
                      }))
                    }
                    required
                    value={campaignForm.channels}
                  />
                </label>

                <label className="field">
                  <span>Brand inputs</span>
                  <input
                    onChange={(event) =>
                      setCampaignForm((currentForm) => ({
                        ...currentForm,
                        brandInputs: event.target.value,
                      }))
                    }
                    value={campaignForm.brandInputs}
                  />
                </label>
              </div>

              <div className="campaign-form-actions">
                <button
                  className="button button-secondary"
                  onClick={() => {
                    setCampaignForm(defaultCampaignForm)
                    setIsCreateCampaignOpen(false)
                  }}
                  type="button"
                >
                  Cancel
                </button>
                <button
                  className="button button-primary"
                  disabled={isCreatingCampaign || !canCreateCampaign(campaignForm)}
                  type="submit"
                >
                  {isCreatingCampaign ? 'Creating...' : 'Create campaign'}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}

      {isBrandAssetModalOpen && selectedCampaign && (
        <div className="modal-backdrop" onMouseDown={closeBrandAssetModal}>
          <section
            aria-labelledby="brand-asset-modal-title"
            aria-modal="true"
            className="campaign-modal brand-asset-modal"
            onMouseDown={(event) => event.stopPropagation()}
            role="dialog"
          >
            <div className="campaign-modal-header">
              <div>
                <span className="eyebrow">Brand library</span>
                <h2 id="brand-asset-modal-title">Manage campaign assets</h2>
              </div>
              <button
                aria-label="Close brand asset manager"
                className="modal-close-button"
                onClick={closeBrandAssetModal}
                type="button"
              >
                x
              </button>
            </div>

            <div className="brand-asset-modal-body">
              <form
                className="brand-upload-panel"
                onSubmit={uploadBrandAssetFromForm}
              >
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">Upload</span>
                    <h2>New library asset</h2>
                  </div>
                </div>

                <label className="field">
                  <span>Name</span>
                  <input
                    autoFocus
                    onChange={(event) =>
                      setBrandAssetForm((currentForm) => ({
                        ...currentForm,
                        name: event.target.value,
                      }))
                    }
                    required
                    value={brandAssetForm.name}
                  />
                </label>

                <label className="field">
                  <span>Type</span>
                  <select
                    onChange={(event) => {
                      const assetType = event.target.value as BrandAssetType
                      setBrandAssetForm((currentForm) => ({
                        ...currentForm,
                        assetType,
                      }))
                      setUploadedBrandAssetRole(
                        defaultCampaignBrandRole(assetType),
                      )
                    }}
                    value={brandAssetForm.assetType}
                  >
                    {brandAssetTypeOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="field">
                  <span>File</span>
                  <input
                    accept=".docx,.jpeg,.jpg,.md,.otf,.pdf,.png,.pptx,.svg,.ttf,.txt,.webp,.woff,.woff2"
                    onChange={(event) =>
                      setBrandAssetFile(event.target.files?.[0] ?? null)
                    }
                    ref={brandAssetFileInputRef}
                    required
                    type="file"
                  />
                </label>

                <label className="field">
                  <span>Description</span>
                  <textarea
                    onChange={(event) =>
                      setBrandAssetForm((currentForm) => ({
                        ...currentForm,
                        description: event.target.value,
                      }))
                    }
                    rows={2}
                    value={brandAssetForm.description}
                  />
                </label>

                <label className="field">
                  <span>Usage guidance</span>
                  <textarea
                    onChange={(event) =>
                      setBrandAssetForm((currentForm) => ({
                        ...currentForm,
                        usageGuidance: event.target.value,
                      }))
                    }
                    rows={3}
                    value={brandAssetForm.usageGuidance}
                  />
                </label>

                <div className="brand-upload-grid">
                  <label className="field">
                    <span>Tags</span>
                    <input
                      onChange={(event) =>
                        setBrandAssetForm((currentForm) => ({
                          ...currentForm,
                          tags: event.target.value,
                        }))
                      }
                      placeholder="approved, evergreen"
                      value={brandAssetForm.tags}
                    />
                  </label>

                  <label className="field">
                    <span>Source URL</span>
                    <input
                      onChange={(event) =>
                        setBrandAssetForm((currentForm) => ({
                          ...currentForm,
                          sourceUrl: event.target.value,
                        }))
                      }
                      type="url"
                      value={brandAssetForm.sourceUrl}
                    />
                  </label>
                </div>

                <label className="brand-attach-toggle">
                  <input
                    checked={attachUploadedBrandAsset}
                    onChange={(event) =>
                      setAttachUploadedBrandAsset(event.target.checked)
                    }
                    type="checkbox"
                  />
                  <span>Attach to {selectedCampaign.name}</span>
                </label>

                {attachUploadedBrandAsset && (
                  <label className="field">
                    <span>Campaign role</span>
                    <select
                      onChange={(event) =>
                        setUploadedBrandAssetRole(event.target.value)
                      }
                      value={uploadedBrandAssetRole}
                    >
                      {campaignBrandRoleOptions.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                )}

                <button
                  className="button button-primary"
                  disabled={
                    isUploadingBrandAsset ||
                    !brandAssetFile ||
                    !brandAssetForm.name.trim()
                  }
                  type="submit"
                >
                  {isUploadingBrandAsset ? 'Uploading...' : 'Upload asset'}
                </button>
              </form>

              <section className="brand-library-panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">Library</span>
                    <h2>Reusable assets</h2>
                  </div>
                  <strong>{brandAssets.length}</strong>
                </div>

                {isLoadingBrandAssets ? (
                  <div className="brand-library-empty">Loading library...</div>
                ) : brandAssets.length === 0 ? (
                  <div className="brand-library-empty">
                    Upload the first reusable brand asset.
                  </div>
                ) : (
                  <div className="brand-library-list">
                    {brandAssets.map((asset) => {
                      const role =
                        brandAssetRoles[asset.id] ??
                        defaultCampaignBrandRole(asset.asset_type)
                      const attachedRoles = campaignBrandAssets
                        .filter((item) => item.brand_asset_id === asset.id)
                        .map((item) => item.role)
                      const isAttachedForRole = attachedRoles.includes(role)

                      return (
                        <article className="brand-library-card" key={asset.id}>
                          <button
                            aria-label={`Open ${asset.name}`}
                            className="brand-asset-preview"
                            onClick={() => void openBrandAsset(asset)}
                            type="button"
                          >
                            {brandAssetPreviewUrls[asset.id] ? (
                              <img
                                alt=""
                                src={brandAssetPreviewUrls[asset.id]}
                              />
                            ) : (
                              <span>{getFileExtension(asset.filename)}</span>
                            )}
                          </button>

                          <div className="brand-library-meta">
                            <div>
                              <strong>{asset.name}</strong>
                              <span>{titleCase(asset.asset_type)}</span>
                            </div>
                            <small>
                              {asset.filename} / {formatFileSize(asset.size_bytes)}
                            </small>
                            {attachedRoles.length > 0 && (
                              <small>
                                Attached as{' '}
                                {attachedRoles.map(displayInputRole).join(', ')}
                              </small>
                            )}
                          </div>

                          <div className="brand-library-actions">
                            <select
                              aria-label={`Role for ${asset.name}`}
                              onChange={(event) =>
                                setBrandAssetRoles((currentRoles) => ({
                                  ...currentRoles,
                                  [asset.id]: event.target.value,
                                }))
                              }
                              value={role}
                            >
                              {campaignBrandRoleOptions.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                            <button
                              className="metadata-button"
                              disabled={
                                isAttachedForRole ||
                                attachingBrandAssetId === asset.id
                              }
                              onClick={() => void attachLibraryBrandAsset(asset)}
                              type="button"
                            >
                              {attachingBrandAssetId === asset.id
                                ? 'Attaching...'
                                : isAttachedForRole
                                  ? 'Attached'
                                  : 'Attach'}
                            </button>
                          </div>
                        </article>
                      )
                    })}
                  </div>
                )}
              </section>
            </div>
          </section>
        </div>
      )}

      <div className="workspace" id="campaigns">
        <aside className="campaign-rail" aria-label="Campaigns">
          <div className="rail-heading">
            <span>Campaigns</span>
            <div className="rail-actions">
              <strong>{isLoadingCampaigns ? '...' : campaigns.length}</strong>
              <button
                aria-expanded={isCreateCampaignOpen}
                aria-haspopup="dialog"
                className="rail-action-button"
                onClick={() => {
                  setIsCreateCampaignOpen(true)
                  setOpenCampaignMenuId(null)
                }}
                type="button"
              >
                New
              </button>
            </div>
          </div>

          <div className="campaign-list">
            {campaigns.map((campaign) => (
              <div
                className={`campaign-card ${
                  campaign.id === selectedCampaignId ? 'is-active' : ''
                }`}
                key={campaign.id}
              >
                <button
                  className="campaign-select"
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

                <div className="campaign-menu">
                  <button
                    aria-expanded={openCampaignMenuId === campaign.id}
                    aria-haspopup="menu"
                    aria-label={`More options for ${campaign.name}`}
                    className="campaign-menu-button"
                    onClick={() =>
                      setOpenCampaignMenuId((currentCampaignId) =>
                        currentCampaignId === campaign.id ? null : campaign.id,
                      )
                    }
                    type="button"
                  >
                    ...
                  </button>

                  {openCampaignMenuId === campaign.id && (
                    <div className="campaign-options-menu" role="menu">
                      <button
                        disabled={deletingCampaignId === campaign.id}
                        onClick={() => void deleteCampaignFromMenu(campaign)}
                        role="menuitem"
                        type="button"
                      >
                        {deletingCampaignId === campaign.id
                          ? 'Deleting...'
                          : 'Delete campaign'}
                      </button>
                    </div>
                  )}
                </div>
              </div>
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

                <section className="campaign-brand-panel" id="library">
                  <div className="campaign-brand-heading">
                    <div>
                      <span>Brand assets</span>
                      <strong>{campaignBrandAssets.length} attached</strong>
                    </div>
                    <button
                      className="metadata-button"
                      onClick={() => setIsBrandAssetModalOpen(true)}
                      type="button"
                    >
                      Manage
                    </button>
                  </div>

                  {isLoadingBrandAssets ? (
                    <div className="campaign-brand-empty">Loading assets...</div>
                  ) : campaignBrandAssets.length === 0 ? (
                    <button
                      className="campaign-brand-empty is-action"
                      onClick={() => setIsBrandAssetModalOpen(true)}
                      type="button"
                    >
                      Attach reusable logos, products, and style references
                    </button>
                  ) : (
                    <div className="campaign-brand-list">
                      {campaignBrandAssets.map((attachment) => (
                        <article
                          className="campaign-brand-card"
                          key={attachment.id}
                        >
                          <button
                            aria-label={`Open ${attachment.brand_asset.name}`}
                            className="campaign-brand-preview"
                            onClick={() =>
                              void openBrandAsset(attachment.brand_asset)
                            }
                            type="button"
                          >
                            {brandAssetPreviewUrls[attachment.brand_asset_id] ? (
                              <img
                                alt=""
                                src={
                                  brandAssetPreviewUrls[
                                    attachment.brand_asset_id
                                  ]
                                }
                              />
                            ) : (
                              <span>
                                {getFileExtension(
                                  attachment.brand_asset.filename,
                                )}
                              </span>
                            )}
                          </button>
                          <div>
                            <strong>{attachment.brand_asset.name}</strong>
                            <span>{displayInputRole(attachment.role)}</span>
                          </div>
                          <button
                            className="campaign-brand-detach"
                            disabled={
                              detachingBrandAssetLinkId === attachment.id
                            }
                            onClick={() =>
                              void detachCampaignBrandAsset(attachment)
                            }
                            type="button"
                          >
                            {detachingBrandAssetLinkId === attachment.id
                              ? '...'
                              : 'Detach'}
                          </button>
                        </article>
                      ))}
                    </div>
                  )}
                </section>

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

                  {renderReferenceImagePicker({
                    images: generationReferenceImages,
                    inputId: 'generation-reference-images',
                    disabled: isGenerating,
                    onAdd: addGenerationReferenceImages,
                    onRemove: removeGenerationReferenceImage,
                    onRoleChange: updateGenerationReferenceRole,
                  })}

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
                      {filteredAssets.map((asset) => {
                        const cardPreviewVersion =
                          getAssetCardPreviewVersion(asset)
                        const cardPreviewUrl = cardPreviewVersion
                          ? getImagePreviewUrl(
                              cardPreviewVersion,
                              artifactPreviewUrls,
                            )
                          : null

                        return (
                          <button
                            className={`asset-card ${
                              selectedAsset?.id === asset.id ? 'is-active' : ''
                            }`}
                            key={asset.id}
                            onClick={() => setSelectedAssetId(asset.id)}
                            type="button"
                          >
                            <span
                              className={`asset-preview ${asset.preview} ${
                                cardPreviewUrl ? 'has-image' : ''
                              }`}
                            >
                              {cardPreviewUrl ? (
                                <img
                                  alt={`${asset.title} generated asset`}
                                  className="asset-preview-image"
                                  src={cardPreviewUrl}
                                />
                              ) : (
                                <>
                                  <span className="preview-band" />
                                  <span className="preview-copy" />
                                  <span className="preview-chip" />
                                </>
                              )}
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
                        )
                      })}
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

                    {latestSelectedVersion ? (
                      <div className="latest-version-preview">
                        <div className="latest-version-heading">
                          <span>Latest version</span>
                          <strong>{latestSelectedVersion.id.toUpperCase()}</strong>
                        </div>
                        {renderArtifactPreview(latestSelectedVersion)}
                        <div
                          className="version-actions latest-version-actions"
                          aria-label={`${latestSelectedVersion.id} actions`}
                        >
                          {renderProvenanceButton(latestSelectedVersion)}
                          <button
                            className="metadata-button"
                            disabled={
                              openingVersionId ===
                              latestSelectedVersion.versionId
                            }
                            onClick={() =>
                              openStoredMetadata(latestSelectedVersion)
                            }
                            type="button"
                          >
                            {openingVersionId ===
                            latestSelectedVersion.versionId
                              ? 'Opening...'
                              : 'Open stored metadata'}
                          </button>
                        </div>
                        {openProvenanceVersionIds[
                          latestSelectedVersion.versionId
                        ] && renderProvenanceDetails(latestSelectedVersion)}
                      </div>
                    ) : (
                      <div className={`detail-preview ${selectedAsset.preview}`}>
                        <span />
                        <strong>{selectedAsset.format}</strong>
                      </div>
                    )}

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

                    <div className="refine-panel">
                      <div className="panel-heading">
                        <div>
                          <span className="eyebrow">Refine</span>
                          <h3>Next version</h3>
                        </div>
                        <span>{`v${getNextVersionNumber(selectedAsset)}`}</span>
                      </div>

                      <label className="field">
                        <span>Prompt</span>
                        <textarea
                          onChange={(event) =>
                            setRefinePrompts((currentPrompts) => ({
                              ...currentPrompts,
                              [selectedAsset.id]: event.target.value,
                            }))
                          }
                          rows={4}
                          value={refinePrompt}
                        />
                      </label>

                      {renderReferenceImagePicker({
                        images: selectedRefineReferenceImages,
                        inputId: `refine-reference-images-${selectedAsset.id}`,
                        disabled: isRefining,
                        onAdd: (files) =>
                          addRefineReferenceImages(selectedAsset.id, files),
                        onRemove: (imageId) =>
                          removeRefineReferenceImage(selectedAsset.id, imageId),
                        onRoleChange: (imageId, role) =>
                          updateRefineReferenceRole(
                            selectedAsset.id,
                            imageId,
                            role,
                          ),
                      })}

                      <button
                        className="button button-primary"
                        disabled={isRefining || !refinePrompt.trim()}
                        onClick={refineAsset}
                        type="button"
                      >
                        {isRefining ? 'Refining...' : 'Create refinement'}
                      </button>
                    </div>

                    <div className="version-list">
                      <h3>Previous Version</h3>
                      {previousSelectedVersions.length > 0 ? (
                        previousSelectedVersions.map((version) => (
                          <div className="version-row" key={version.versionId}>
                            <span className="version-title">
                              <strong>{version.id.toUpperCase()}</strong>
                              {version.label}
                            </span>
                            <span className="version-provider">
                              {version.created}
                            </span>
                            <code>{version.storageKey}</code>
                            {renderArtifactPreview(version)}
                            <div
                              className="version-actions"
                              aria-label={`${version.id} actions`}
                            >
                              {renderProvenanceButton(version)}
                              <label
                                aria-disabled={
                                  uploadingArtifactVersionId ===
                                  version.versionId
                                }
                                className={`metadata-button artifact-upload ${
                                  uploadingArtifactVersionId ===
                                  version.versionId
                                    ? 'is-disabled'
                                    : ''
                                }`}
                              >
                                {uploadingArtifactVersionId === version.versionId
                                  ? 'Uploading...'
                                  : version.artifactStorageKey
                                    ? 'Replace artifact'
                                    : 'Attach output'}
                                <input
                                  disabled={
                                    uploadingArtifactVersionId ===
                                    version.versionId
                                  }
                                  onChange={(event) => {
                                    const file =
                                      event.currentTarget.files?.[0] ?? null
                                    event.currentTarget.value = ''
                                    void uploadArtifact(version, file)
                                  }}
                                  type="file"
                                />
                              </label>
                              <button
                                className="metadata-button"
                                disabled={
                                  !version.artifactStorageKey ||
                                  openingArtifactVersionId === version.versionId
                                }
                                onClick={() => openArtifact(version)}
                                type="button"
                              >
                                {openingArtifactVersionId === version.versionId
                                  ? 'Opening...'
                                  : 'Open artifact'}
                              </button>
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
                            {openProvenanceVersionIds[version.versionId] &&
                              renderProvenanceDetails(version)}
                          </div>
                        ))
                      ) : (
                        <div className="empty-state">No previous versions yet.</div>
                      )}
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
