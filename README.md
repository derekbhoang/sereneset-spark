# SereneSet Spark (SSS)

An AI campaign asset workspace for marketing teams that turns campaign briefs and brand assets into brand-guided campaign packs: copy, images, and video concepts. Every generated asset is stored with version history, prompts, model metadata, review status, and export options, so teams can approve, refine, reuse, and audit their creative work over time.

## Problem

Marketing teams are under pressure to create more campaign assets for more channels, but the process is still fragmented. Briefs, brand guidelines, generated drafts, feedback, approvals, and final files often live across separate tools, making it hard to keep assets on-brand and understand how each version was created.

As teams adopt generative AI, this problem gets sharper: outputs can be fast, but they are often disconnected from brand context, review workflows, metadata, and long-term asset storage. Teams need a way to generate campaign materials while preserving version history, approval status, prompts, model details, and reusable campaign context.

## Solution

SereneSet Spark gives marketing teams a single workspace to turn campaign briefs and brand assets into structured, brand-guided campaign packs. Teams can generate copy, images, and video concepts from shared campaign context, then review, refine, approve, and export assets without losing the history behind each version.

Every generated asset is stored with useful metadata, including prompts, model details, campaign tags, brand inputs, review status, and version lineage. This makes the creative process easier to audit, reuse, and improve over time while helping teams move faster without losing control of brand consistency.

## How It Works

1. Teams create a dedicated campaign workspace for each launch, product, audience, or channel initiative. Each campaign keeps its own briefs, generated assets, feedback, approvals, metadata, and export history separated from other campaigns so the work stays easy to organize and review.

2. Teams add the campaign context, such as the brief, goals, audience, tone, channels, brand assets, and creative requirements. SereneSet Spark uses this context to guide the generation of campaign copy, images, and video concepts.

3. Teams review generated assets inside the campaign workspace. They can approve strong assets, request refinements, compare versions, and keep a clear record of how each asset changed over time.

4. Teams export approved campaign assets for publishing, handoff, or future reuse. Each exported asset remains connected to its campaign metadata, version history, prompts, model details, and review status.

5. Optionally, teams can create a shared library for reusable brand details, such as guidelines, product descriptions, tone of voice, audience profiles, disclaimers, logos, and reference assets. Each campaign can use this shared library to stay consistent without duplicating the same brand information every time.

## Key Features

- **Separated campaign workspaces:** Create and manage multiple campaigns independently, with each campaign keeping its own briefs, assets, approvals, metadata, and exports.
- **Shared brand library:** Store reusable brand guidelines, product details, tone of voice, audience profiles, and reference assets that every campaign can use.
- **Brand-guided asset generation:** Generate campaign copy, images, and video concepts using the campaign brief and selected brand context.
- **Version history:** Keep track of every refinement, regenerated asset, and approved version so teams can see how creative work changed over time.
- **Review and approval workflow:** Mark assets as drafts, in review, approved, or rejected to support clearer collaboration between marketers, designers, and stakeholders.
- **Metadata-rich storage:** Save prompts, model details, campaign tags, brand inputs, review status, and asset lineage alongside each generated file.
- **Searchable asset organization:** Find assets by campaign, channel, status, format, audience, or tag for future review and reuse.
- **Export-ready campaign packs:** Package approved assets for publishing, stakeholder handoff, or reuse in future campaigns.

## Tech Stack

- **Frontend:** React, TypeScript, and Tailwind CSS for a fast, responsive campaign workspace.
- **Backend:** FastAPI for the application API, campaign workflows, asset metadata, and generation requests.
- **Generative media orchestration:** Genblaze for connecting campaign context to AI media generation workflows.
- **Storage:** Backblaze B2 Cloud Storage for generated assets, uploaded brand files, campaign exports, and versioned media.
- **Metadata:** Structured campaign, asset, prompt, model, review, and version metadata stored alongside each asset.
- **AI providers:** GMI Cloud and OpenAI for generating campaign copy, images, and video concepts.

## MVP Scope

The first version of SereneSet Spark focuses on a complete campaign asset workflow that can be demoed end to end:

1. Create and manage multiple separated campaign workspaces.
2. Add campaign details, including brief, audience, tone, channels, goals, and brand requirements.
3. Create an optional shared brand library with reusable guidelines, product details, audience profiles, and reference assets.
4. Generate campaign copy and image concepts using GMI Cloud and OpenAI.
5. Store generated assets and uploaded brand files in Backblaze B2 Cloud Storage.
6. Save useful metadata for each asset, including prompts, model details, campaign tags, version history, and review status.
7. Review generated assets and mark them as draft, in review, approved, or rejected.
8. Refine selected assets while preserving earlier versions.
9. Search and filter assets by campaign, channel, status, format, audience, or tag.
10. Export approved assets as a campaign pack for handoff or publishing.

Video concept generation can be included as a stretch goal after the copy, image, storage, review, and export workflow is working reliably.
