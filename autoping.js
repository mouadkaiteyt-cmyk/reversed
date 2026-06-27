const https = require('https');
const http = require('http');

const url = process.env.RENDER_EXTERNAL_URL || 'http://localhost:8000';
const interval = 5000; // 5 ثواني

console.log(`Starting autoping for ${url} every 5 seconds...`);

setInterval(() => {
  const client = url.startsWith('https') ? https : http;
  client.get(url, (res) => {
    console.log(`[${new Date().toISOString()}] Pinged ${url} - Status: ${res.statusCode}`);
    
    // تفريغ البيانات لتجنب تسرب الذاكرة (Memory Leak)
    res.on('data', () => {});
    res.on('end', () => {});
  }).on('error', (err) => {
    console.error(`[${new Date().toISOString()}] Error pinging ${url}: ${err.message}`);
  });
}, interval);
