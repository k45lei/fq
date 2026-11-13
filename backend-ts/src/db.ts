import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient, Prisma } from './generated/prisma/client.js';
export { PrismaClient, Prisma };
export type Tx = Prisma.TransactionClient;
export function database(url: string) {
  return new PrismaClient({ adapter: new PrismaPg({ connectionString: url }) });
}
// Lock the attempt for submissions, ownership changes and payment grants. The row lock
// prevents duplicate submissions and changing ownership halfway through granting access.
export async function lockAttempt(tx: Tx, id: string) {
  await tx.$queryRaw`SELECT id FROM attempts WHERE id = ${id}::uuid FOR UPDATE`;
}
