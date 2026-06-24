# Bayside House Dashboard - Vercel Deployment Guide

## Overview
Your dashboard is now password-protected and ready to deploy to Vercel (free hosting).

**Password:** `Puntroadhouse14`

## Deployment Steps

### 1. Create a GitHub Repository
1. Go to [github.com/new](https://github.com/new)
2. Repository name: `bayside-dashboard`
3. Description: `Bayside House Operations Dashboard`
4. Choose **Public** (free plan requirement)
5. Click "Create repository"

### 2. Push Your Code to GitHub
After creating the GitHub repo, run these commands in PowerShell (in your Bayside Automations folder):

```powershell
cd "C:\Users\Reception\OneDrive - Bayside House\Desktop\Bayside Automations"

# Add remote (replace YOUR_USERNAME with your GitHub username)
git remote add origin https://github.com/YOUR_USERNAME/bayside-dashboard.git
git branch -M main
git push -u origin main
```

### 3. Deploy to Vercel
1. Go to [vercel.com/new](https://vercel.com/new)
2. Sign up with your GitHub account
3. Click "Import Project"
4. Select your `bayside-dashboard` repository
5. Click "Import"
6. Settings:
   - **Framework:** "Other" (it's a static HTML file)
   - Leave other settings as default
7. Click "Deploy"
8. Wait for deployment to complete (~1 minute)
9. You'll get a live URL like: `https://bayside-dashboard-xyz.vercel.app`

## Features

✅ **Password Protected** - Requires `Puntroadhouse14` to access
✅ **Free Hosting** - Vercel's free tier is unlimited
✅ **Live URL** - Get a public link to share with your team
✅ **Mobile Responsive** - Works on all devices
✅ **Session Persistence** - Stay logged in during your session
✅ **Auto-refresh** - Page refresh keeps you logged in

## After Deployment

### Updating the Dashboard
1. Edit `Bayside_Dashboard.html` locally
2. Commit and push to GitHub:
   ```powershell
   git add Bayside_Dashboard.html
   git commit -m "Update dashboard metrics"
   git push
   ```
3. Vercel automatically deploys within seconds

### Updating the Password
To change the password:
1. Open `Bayside_Dashboard.html`
2. Find this line (around line 520): `const CORRECT_PASSWORD = 'Puntroadhouse14';`
3. Change to your new password
4. Commit and push to GitHub
5. Vercel redeploys automatically

### Sharing with Your Team
Share the Vercel URL with team members - they just need the password to access the dashboard.

## Troubleshooting

**Dashboard not showing after password?**
- Clear browser cache (Ctrl+Shift+Delete)
- Try in an incognito/private window

**Vercel won't connect to GitHub?**
- Make sure your GitHub account is public
- Try disconnecting and reconnecting in Vercel settings

**Password not working?**
- Make sure there are no extra spaces
- Check the exact password in the HTML file
- Clear session storage: Press F12 → Console → `sessionStorage.clear()`

## Need Help?
- Vercel docs: https://vercel.com/docs
- GitHub docs: https://docs.github.com
