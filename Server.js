// OPEN SOURCE PUBLIC AD SERVER - 100% Public Lifetime Save
// npm install express multer cors
// node server.js
const express = require('express');
const multer = require('multer');
const cors = require('cors');
const fs = require('fs');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

const UPLOAD_DIR = path.join(__dirname, 'uploads');
const DATA_FILE = path.join(__dirname, 'data.json');

// Create folders/files if not exist - lifetime persistence
if (!fs.existsSync(UPLOAD_DIR)) fs.mkdirSync(UPLOAD_DIR, { recursive: true });
if (!fs.existsSync(DATA_FILE)) {
  fs.writeFileSync(DATA_FILE, JSON.stringify({ products: [], orders: [] }, null, 2));
  console.log('Created data.json for lifetime storage');
}

app.use('/uploads', express.static(UPLOAD_DIR));
app.use(express.static(__dirname));

function readData() {
  try { return JSON.parse(fs.readFileSync(DATA_FILE, 'utf8')); } 
  catch { return { products: [], orders: [] }; }
}
function writeData(data) {
  fs.writeFileSync(DATA_FILE, JSON.stringify(data, null, 2));
}

const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, UPLOAD_DIR),
  filename: (req, file, cb) => {
    const unique = Date.now() + '-' + Math.round(Math.random()*1e9);
    cb(null, unique + path.extname(file.originalname));
  }
});
const upload = multer({ storage, limits: { fileSize: 20*1024*1024 } });

// PUBLIC API: সবাই এখান থেকে দেখবে
app.get('/api/data', (req, res) => {
  res.set('Cache-Control', 'no-store');
  res.json(readData());
});

// PUBLIC UPLOAD: আপনি আপলোড করলে সার্ভারে সেভ, সবাই দেখবে
app.post('/api/add-product', upload.single('image'), (req, res) => {
  const { name, price } = req.body;
  if (!name || !price || !req.file) {
    return res.status(400).json({ success: false, error: 'Missing fields' });
  }
  const data = readData();
  const product = {
    id: Date.now(),
    name: name.trim(),
    price: price.trim(),
    img: `/uploads/${req.file.filename}`, // PUBLIC LINK
    timestamp: new Date().toLocaleString('bn-BD'),
    createdAt: new Date().toISOString()
  };
  data.products.unshift(product);
  writeData(data);
  console.log(`[PUBLIC SAVE] ${product.name} -> ${product.img} - Now visible to everyone`);
  res.json({ success: true, product });
});

app.delete('/api/delete-product/:id', (req, res) => {
  const id = parseInt(req.params.id);
  const data = readData();
  const prod = data.products.find(p=>p.id===id);
  if (prod && prod.img.startsWith('/uploads/')) {
    const fp = path.join(__dirname, prod.img);
    if (fs.existsSync(fp)) fs.unlinkSync(fp);
  }
  data.products = data.products.filter(p=>p.id!==id);
  writeData(data);
  res.json({ success: true });
});

app.post('/api/order', (req,res)=>{
  const data = readData();
  data.orders.unshift({ ...req.body, timestamp: new Date().toLocaleString('bn-BD') });
  writeData(data);
  res.json({success:true});
});

app.post('/api/fetch', (req,res)=>{
  res.json({success:false, error:'fetch logic here'});
});

app.get('/', (req,res)=> res.sendFile(path.join(__dirname, 'index_1.html')));

app.listen(PORT, ()=> {
  console.log(`\n✅ SERVER RUNNING: http://localhost:${PORT}`);
  console.log(`📂 Public images: ${UPLOAD_DIR}`);
  console.log(`💾 Lifetime DB: ${DATA_FILE}`);
  console.log(`🌍 PUBLIC MODE: Anyone uploading will be visible to all users\n`);
});
