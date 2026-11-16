// Licence offers (one-time delivery) and trial-extension requests.
//
// An offer is a signed licence the vendor issued for one site. The factory
// picks it up on its next heartbeat, installs it, and acknowledges the serial
// on the following heartbeat. Once acked it is never sent again (one-time key).
// Offers are delivered in order; an unacked offer is re-sent until acked.

import type { D1Like } from "./db";

export interface OfferRow {
  id: string;
  site_id: string;
  serial: string;
  packager_fp: string;
  licence_json: string;
  created_at: string;
  acked_at: string | null;
}

export interface TrialRequestRow {
  id: string;
  site_id: string;
  days: number;
  reason: string;
  status: string;
  requested_at: string;
  decided_at: string | null;
  decided_by: string | null;
}

export class OfferStore {
  private db: D1Like;
  constructor(db: D1Like) {
    this.db = db;
  }

  async addOffer(row: Omit<OfferRow, "acked_at">): Promise<void> {
    await this.db
      .prepare("INSERT INTO licence_offers(id,site_id,serial,packager_fp,licence_json,created_at,acked_at) VALUES(?,?,?,?,?,?,NULL)")
      .bind(row.id, row.site_id, row.serial, row.packager_fp, row.licence_json, row.created_at)
      .run();
  }

  async pendingOffer(siteId: string): Promise<OfferRow | null> {
    return this.db
      .prepare("SELECT * FROM licence_offers WHERE site_id=? AND acked_at IS NULL ORDER BY created_at ASC LIMIT 1")
      .bind(siteId)
      .first<OfferRow>();
  }

  async ackOffer(siteId: string, serial: string, at: string): Promise<void> {
    await this.db
      .prepare("UPDATE licence_offers SET acked_at=? WHERE site_id=? AND serial=? AND acked_at IS NULL")
      .bind(at, siteId, serial)
      .run();
  }

  async addTrialRequest(row: Omit<TrialRequestRow, "status" | "decided_at" | "decided_by">): Promise<void> {
    await this.db
      .prepare("INSERT INTO trial_requests(id,site_id,days,reason,status,requested_at,decided_at,decided_by) VALUES(?,?,?,?,'pending',?,NULL,NULL)")
      .bind(row.id, row.site_id, row.days, row.reason, row.requested_at)
      .run();
  }

  async listTrialRequests(status?: string): Promise<TrialRequestRow[]> {
    const r = status
      ? await this.db.prepare("SELECT * FROM trial_requests WHERE status=? ORDER BY requested_at DESC").bind(status).all<TrialRequestRow>()
      : await this.db.prepare("SELECT * FROM trial_requests ORDER BY requested_at DESC").bind().all<TrialRequestRow>();
    return r.results;
  }

  async getTrialRequest(id: string): Promise<TrialRequestRow | null> {
    return this.db.prepare("SELECT * FROM trial_requests WHERE id=?").bind(id).first<TrialRequestRow>();
  }

  async decideTrialRequest(id: string, status: string, by: string, at: string): Promise<void> {
    await this.db
      .prepare("UPDATE trial_requests SET status=?, decided_at=?, decided_by=? WHERE id=? AND status='pending'")
      .bind(status, at, by, id)
      .run();
  }
}
