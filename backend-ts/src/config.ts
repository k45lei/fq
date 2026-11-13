import { z } from 'zod';

const schema = z.object({
  DATABASE_URL: z.url().refine(v => /^postgres(ql)?:/.test(v), 'Use a PostgreSQL URL'),
  SESSION_KEY: z.string().regex(/^[a-fA-F0-9]{64}$/, 'SESSION_KEY must be 32 bytes encoded as hex'),
  APP_ORIGIN: z.url().default('http://localhost:5173'),
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  PORT: z.coerce.number().int().positive().default(8000),
  GOOGLE_CLIENT_ID: z.string().default(''),
  GOOGLE_CLIENT_SECRET: z.string().default(''),
  STRIPE_SECRET_KEY: z.string().default(''),
  STRIPE_PRICE_ID: z.string().default(''),
  STRIPE_WEBHOOK_SECRET: z.string().default(''),
  RESULT_API_SECRET: z.string().default(''),
});

export type Config = z.infer<typeof schema>;

export const configuration = (env: NodeJS.ProcessEnv = process.env): Config => {
  const config = schema.parse(env);
  config.APP_ORIGIN = new URL(config.APP_ORIGIN).origin;

  if (config.NODE_ENV === 'production' && !config.APP_ORIGIN.startsWith('https://')) {
    throw new Error('Production APP_ORIGIN must use HTTPS');
  }

  if (config.RESULT_API_SECRET && config.RESULT_API_SECRET.length < 32) {
    throw new Error('RESULT_API_SECRET must be at least 32 characters');
  }

  return config;
};
