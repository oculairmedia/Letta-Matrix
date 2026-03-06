import pg from "pg";

export interface RegistrationRow {
  directory: string;
  rooms: string[];
  identity_mxids: string[];
  session_id: string | null;
  created_at: Date;
  updated_at: Date;
}

export class RegistrationDB {
  private pool: pg.Pool;

  constructor(databaseUrl: string) {
    this.pool = new pg.Pool({
      connectionString: databaseUrl,
      max: 3,
    });
  }

  async init(): Promise<void> {
    await this.pool.query(`
      CREATE TABLE IF NOT EXISTS opencode_registrations (
        directory VARCHAR(500) PRIMARY KEY,
        rooms JSONB NOT NULL DEFAULT '[]',
        identity_mxids JSONB NOT NULL DEFAULT '[]',
        session_id VARCHAR(255),
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
      );
    `);
  }

  async upsert(
    directory: string,
    rooms: string[],
    identityMxids: string[],
    sessionId: string
  ): Promise<void> {
    await this.pool.query(
      `
        INSERT INTO opencode_registrations (directory, rooms, identity_mxids, session_id)
        VALUES ($1, $2::jsonb, $3::jsonb, $4)
        ON CONFLICT (directory)
        DO UPDATE SET
          rooms = EXCLUDED.rooms,
          identity_mxids = EXCLUDED.identity_mxids,
          session_id = EXCLUDED.session_id,
          updated_at = NOW()
      `,
      [directory, JSON.stringify(rooms), JSON.stringify(identityMxids), sessionId]
    );
  }

  async getAll(): Promise<RegistrationRow[]> {
    const result = await this.pool.query<RegistrationRow>(
      `
        SELECT directory, rooms, identity_mxids, session_id, created_at, updated_at
        FROM opencode_registrations
      `
    );
    return result.rows;
  }

  async remove(directory: string): Promise<void> {
    await this.pool.query("DELETE FROM opencode_registrations WHERE directory = $1", [directory]);
  }

  async updateRooms(directory: string, rooms: string[]): Promise<void> {
    await this.pool.query(
      `
        UPDATE opencode_registrations
        SET rooms = $2::jsonb,
            updated_at = NOW()
        WHERE directory = $1
      `,
      [directory, JSON.stringify(rooms)]
    );
  }
}
