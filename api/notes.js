const { google } = require('googleapis');
const fs = require('fs');

const SHEET_ID = process.env.GOOGLE_SHEET_ID;
const TAB = 'Notes';
const MAX_NOTE_LEN = 500;
const MAX_NAME_LEN = 60;

// Production (Vercel): GOOGLE_SERVICE_ACCOUNT_B64 holds the service account
// key JSON, base64-encoded (env vars don't handle raw multi-line JSON well).
// Local dev: falls back to GOOGLE_SERVICE_ACCOUNT_JSON as a file path, same
// convention the Python scripts in this repo already use, so `vercel dev`
// works against the same .env without extra setup.
function loadCredentials() {
  if (process.env.GOOGLE_SERVICE_ACCOUNT_B64) {
    const raw = Buffer.from(process.env.GOOGLE_SERVICE_ACCOUNT_B64, 'base64').toString('utf8');
    return JSON.parse(raw);
  }
  const path = process.env.GOOGLE_SERVICE_ACCOUNT_JSON || 'service_account.json';
  return JSON.parse(fs.readFileSync(path, 'utf8'));
}

function getSheets() {
  const creds = loadCredentials();
  const auth = new google.auth.JWT(
    creds.client_email, null, creds.private_key,
    ['https://www.googleapis.com/auth/spreadsheets']
  );
  return google.sheets({ version: 'v4', auth });
}

function escapeHtmlLike(s) {
  // Strip control characters only -- display-side escaping happens in the
  // browser when rendering, this just keeps the sheet clean.
  return String(s).replace(/[\r\n\t]+/g, ' ').trim();
}

module.exports = async (req, res) => {
  let sheets;
  try {
    sheets = getSheets();
  } catch (err) {
    console.error('Notes API: failed to load credentials', err);
    res.status(500).json({ error: 'Server is not configured for notes yet.' });
    return;
  }

  try {
    if (req.method === 'GET') {
      const result = await sheets.spreadsheets.values.get({
        spreadsheetId: SHEET_ID,
        range: `${TAB}!A2:D`,
      });
      const rows = result.data.values || [];
      const notes = rows
        .map((r) => ({
          timestamp: r[0] || '',
          name: r[1] || '',
          note: r[2] || '',
          resolved: String(r[3] || '').trim().toUpperCase() === 'TRUE',
        }))
        .filter((n) => n.note && !n.resolved)
        .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
        .slice(0, 50);
      res.status(200).json({ notes });
      return;
    }

    if (req.method === 'POST') {
      const body = req.body || {};
      const note = escapeHtmlLike(body.note).slice(0, MAX_NOTE_LEN);
      const name = escapeHtmlLike(body.name).slice(0, MAX_NAME_LEN) || 'Anonymous';
      if (!note) {
        res.status(400).json({ error: 'Write a note first.' });
        return;
      }
      const timestamp = new Date().toISOString();
      await sheets.spreadsheets.values.append({
        spreadsheetId: SHEET_ID,
        range: `${TAB}!A2:D`,
        valueInputOption: 'RAW',
        insertDataOption: 'INSERT_ROWS',
        requestBody: { values: [[timestamp, name, note, '']] },
      });
      res.status(201).json({ timestamp, name, note });
      return;
    }

    res.status(405).json({ error: 'Method not allowed' });
  } catch (err) {
    console.error('Notes API error', err);
    res.status(500).json({ error: 'Something went wrong saving that note.' });
  }
};
