import { defineTool, type AnyToolDefinition } from '@agent-tool-platform/runtime/tools';
import { z } from 'zod';
import { supportedLanguages } from '../config.js';
import type { VisionServices } from '../capability.js';

export const imageReferenceSchema = z.discriminatedUnion('kind', [
  z
    .object({
      kind: z.literal('local_path'),
      path: z.string().min(1).max(4096),
    })
    .strict(),
  z
    .object({
      kind: z.literal('asset'),
      assetId: z
        .string()
        .min(8)
        .max(128)
        .regex(/^[A-Za-z0-9_-]+$/u),
    })
    .strict(),
]);

const dimensionsSchema = z
  .object({
    width: z.number().int().positive().max(100_000),
    height: z.number().int().positive().max(100_000),
  })
  .strict();

const boundingBoxSchema = z
  .object({
    x: z.number().int().nonnegative().max(1_000_000),
    y: z.number().int().nonnegative().max(1_000_000),
    width: z.number().int().positive().max(1_000_000),
    height: z.number().int().positive().max(1_000_000),
  })
  .strict();

const normalizedBoxSchema = z
  .object({
    x: z.number().min(0).max(1),
    y: z.number().min(0).max(1),
    width: z.number().min(0).max(1),
    height: z.number().min(0).max(1),
  })
  .strict();

const resultMetaSchema = z
  .object({
    warnings: z.array(z.string().max(300)).max(20),
    truncated: z.boolean(),
  })
  .strict();

const processingModeSchema = z.enum(['auto', 'local', 'azure']);
const providerSchema = z
  .object({
    name: z.enum(['embedded_svg_text', 'local_paddleocr', 'azure_content_understanding']),
    mode: processingModeSchema,
    model: z.string().max(120).nullable(),
    apiVersion: z.string().max(40).nullable(),
  })
  .strict();

const textBlockSchema = z
  .object({
    id: z.string().min(1).max(32),
    type: z.enum(['line', 'paragraph', 'word']),
    text: z.string().max(4000),
    page: z.number().int().min(1).max(1000),
    box: normalizedBoxSchema.nullable(),
    polygon: z.array(z.number()).max(32).nullable(),
    confidence: z.number().min(0).max(1).nullable(),
  })
  .strict();

export const extractTextInputSchema = z
  .object({
    image: imageReferenceSchema,
    outputFormat: z.enum(['markdown', 'text', 'csv']).default('markdown'),
    language: z.enum(supportedLanguages).optional(),
    includeCoordinates: z.boolean().default(true),
    processingMode: processingModeSchema.default('auto'),
  })
  .strict();

export const extractTextOutputSchema = z
  .object({
    content: z.string().max(200_000),
    blocks: z.array(textBlockSchema).max(500),
    dimensions: dimensionsSchema,
    provider: providerSchema,
    fallbackUsed: z.boolean(),
    meta: resultMetaSchema,
  })
  .strict();

export const compareImagesInputSchema = z
  .object({
    before: imageReferenceSchema,
    after: imageReferenceSchema,
    threshold: z.number().min(0).max(1).default(0.05),
    includeDiff: z.boolean().default(false),
    maxRegions: z.number().int().min(1).max(100).default(20),
  })
  .strict();

const changedRegionSchema = z
  .object({
    box: boundingBoxSchema,
    changedPixels: z.number().int().positive(),
  })
  .strict();

export const compareImagesOutputSchema = z
  .object({
    similarityScore: z.number().min(0).max(1),
    beforeDimensions: dimensionsSchema,
    afterDimensions: dimensionsSchema,
    dimensionsChanged: z.boolean(),
    comparisonDimensions: dimensionsSchema,
    changedPixels: z.number().int().nonnegative(),
    changedRatio: z.number().min(0).max(1),
    regions: z.array(changedRegionSchema).max(100),
    diffArtifactId: z.string().max(128).nullable(),
    meta: resultMetaSchema,
  })
  .strict();

export const optimizeImageInputSchema = z
  .object({
    image: imageReferenceSchema,
    box: boundingBoxSchema,
    maxWidth: z.number().int().min(16).max(8192).default(1024),
    maxHeight: z.number().int().min(16).max(8192).default(1024),
    quality: z.number().int().min(1).max(100).default(80),
    outputFormat: z.enum(['png', 'jpeg', 'webp']).default('webp'),
  })
  .strict();

export const optimizeImageOutputSchema = z
  .object({
    artifactId: z.string().min(8).max(128),
    originalDimensions: dimensionsSchema,
    cropDimensions: dimensionsSchema,
    outputDimensions: dimensionsSchema,
    byteCount: z.number().int().positive(),
    originalByteCount: z.number().int().positive(),
    format: z.enum(['png', 'jpeg', 'webp']),
    normalizedBox: normalizedBoxSchema,
    meta: resultMetaSchema,
  })
  .strict();

const factProvenanceSchema = z
  .object({
    source: z.enum(['embedded_svg_text', 'ocr']),
    blockIds: z.array(z.string().min(1).max(32)).min(1).max(20),
  })
  .strict();

const analysisFactSchema = z
  .object({
    kind: z.enum([
      'identity',
      'revision',
      'environment',
      'listener',
      'ingress',
      'readiness',
      'status',
    ]),
    label: z.string().min(1).max(120),
    value: z.string().min(1).max(1000),
    evidence: z.string().min(1).max(4000),
    confidence: z.number().min(0).max(1),
    box: normalizedBoxSchema.nullable(),
    provenance: factProvenanceSchema,
  })
  .strict();

export const analyzeImageInputSchema = z
  .object({
    image: imageReferenceSchema,
    language: z.enum(supportedLanguages).optional(),
    processingMode: processingModeSchema.default('auto'),
    maxFacts: z.number().int().min(1).max(100).default(50),
  })
  .strict();

export const analyzeImageOutputSchema = z
  .object({
    summary: z.string().max(20_000),
    extractedText: z.string().max(200_000),
    facts: z.array(analysisFactSchema).max(100),
    dimensions: dimensionsSchema,
    provider: providerSchema,
    analysisMode: z.enum(['embedded_svg_text', 'ocr']),
    fallbackUsed: z.boolean(),
    fallbackReason: z.string().max(500).nullable(),
    meta: resultMetaSchema,
  })
  .strict();

export type AnalyzeImageInput = z.infer<typeof analyzeImageInputSchema>;
export type AnalyzeImageOutput = z.infer<typeof analyzeImageOutputSchema>;
export type ExtractTextInput = z.infer<typeof extractTextInputSchema>;
export type ExtractTextOutput = z.infer<typeof extractTextOutputSchema>;
export type CompareImagesInput = z.infer<typeof compareImagesInputSchema>;
export type CompareImagesOutput = z.infer<typeof compareImagesOutputSchema>;
export type OptimizeImageInput = z.infer<typeof optimizeImageInputSchema>;
export type OptimizeImageOutput = z.infer<typeof optimizeImageOutputSchema>;

export const analyzeImageTool = defineTool({
  name: 'analyze_image',
  title: 'Analyze an image or figure',
  summary: 'Extract bounded structured facts and supporting evidence from an image or figure.',
  description:
    'Uses embedded SVG text or OCR to identify status, identity, environment, listener, ingress, and readiness evidence without parsing a surrounding document.',
  kind: 'read',
  routing: {
    useWhen: [
      'a screenshot, dashboard, portal capture, diagram, or extracted document figure contains operational facts to interpret',
      'you need structured visual evidence with confidence and provenance rather than raw OCR alone',
    ],
    doNotUseWhen: [
      'you only need verbatim text or reading order; use extract_text_and_layout',
      'you need surrounding document prose parsed or retrieved; use a document capability',
      'you need open-ended photographic scene understanding unsupported by text and layout evidence',
    ],
    scope:
      'one bounded local image beneath a configured root, or one principal-scoped worker artifact',
    changesState: false,
  },
  annotations: {
    readOnlyHint: true,
    destructiveHint: false,
    idempotentHint: true,
    openWorldHint: true,
  },
  inputSchema: analyzeImageInputSchema,
  outputSchema: analyzeImageOutputSchema,
  handler(input, services: VisionServices, context) {
    return services.worker.invoke('analyze_image', input, analyzeImageOutputSchema, context);
  },
});

export const extractTextTool = defineTool({
  name: 'extract_text_and_layout',
  title: 'Extract text and layout',
  summary: 'Extract OCR text, reading order, confidence, and normalized coordinates.',
  description:
    'Normalizes local PaddleOCR or Azure Content Understanding output into bounded text blocks.',
  kind: 'read',
  routing: {
    useWhen: [
      'the requested result is text, reading order, a table, or coordinates from an image',
      'OCR can avoid sending a full image to native model vision',
    ],
    doNotUseWhen: [
      'you need operational facts interpreted from the extracted text; use analyze_image',
      'you need before/after pixel changes; use compare_images',
    ],
    scope:
      'one bounded local raster image beneath a configured root, or one principal-scoped worker artifact',
    changesState: false,
  },
  annotations: {
    readOnlyHint: true,
    destructiveHint: false,
    idempotentHint: true,
    openWorldHint: true,
  },
  inputSchema: extractTextInputSchema,
  outputSchema: extractTextOutputSchema,
  handler(input, services: VisionServices, context) {
    return services.worker.invoke(
      'extract_text_and_layout',
      input,
      extractTextOutputSchema,
      context,
    );
  },
});

export const compareImagesTool = defineTool({
  name: 'compare_images',
  title: 'Compare images',
  summary: 'Compare two images and return deterministic changed pixels and regions.',
  description:
    'Performs a bounded local pixel comparison and can produce a principal-scoped ephemeral diff artifact.',
  kind: 'write',
  routing: {
    useWhen: [
      'you need to determine whether a before and after screenshot changed',
      'you need changed regions before selecting a smaller crop',
    ],
    doNotUseWhen: [
      'you need an explanation of what the changed content means; use analyze_image on the relevant image',
      'you need OCR text from a single image; use extract_text_and_layout',
    ],
    scope: 'two bounded local raster images or worker artifacts',
    changesState: true,
  },
  annotations: {
    readOnlyHint: false,
    destructiveHint: false,
    idempotentHint: false,
    openWorldHint: false,
  },
  inputSchema: compareImagesInputSchema,
  outputSchema: compareImagesOutputSchema,
  handler(input, services: VisionServices, context) {
    return services.worker.invoke('compare_images', input, compareImagesOutputSchema, context);
  },
});

export const optimizeImageTool = defineTool({
  name: 'optimize_image_region',
  title: 'Optimize an image region',
  summary: 'Crop, downscale, and compress a known image region into an ephemeral artifact.',
  description:
    'Creates a bounded principal-scoped worker artifact for a caller-supplied pixel box and never modifies the source image.',
  kind: 'write',
  routing: {
    useWhen: [
      'useful pixel coordinates are already known and a smaller image will reduce model tokens',
      'a changed region or OCR box should be cropped before further inspection',
    ],
    doNotUseWhen: [
      'you do not know which region matters; use analyze_image, extract_text_and_layout, or compare_images first',
      'you need to locate an object without coordinates',
    ],
    nextSteps: ['analyze_image'],
    scope: 'one bounded local raster image or worker artifact plus an in-bounds pixel box',
    changesState: true,
  },
  annotations: {
    readOnlyHint: false,
    destructiveHint: false,
    idempotentHint: false,
    openWorldHint: false,
  },
  inputSchema: optimizeImageInputSchema,
  outputSchema: optimizeImageOutputSchema,
  handler(input, services: VisionServices, context) {
    return services.worker.invoke(
      'optimize_image_region',
      input,
      optimizeImageOutputSchema,
      context,
    );
  },
});

export const capabilityTools: readonly AnyToolDefinition<VisionServices>[] = [
  analyzeImageTool,
  extractTextTool,
  compareImagesTool,
  optimizeImageTool,
];
