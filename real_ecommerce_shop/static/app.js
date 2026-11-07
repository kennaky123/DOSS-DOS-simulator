// JavaScript xử lý giao diện Web Bán Hàng E-Commerce và kết nối SQLite

let allProducts = [];
let cart = [];
let currentUser = null;

// Khởi chạy khi load trang
document.addEventListener("DOMContentLoaded", () => {
  loadProducts();
  loadCart();
  checkAuthStatus();
  startServerMonitoring();
  setupEventListeners();
});

// Format tiền tệ VND
function formatVND(amount) {
  return new Intl.NumberFormat("vi-VN").format(amount) + " đ";
}

// 1. Tải danh sách sản phẩm từ SQLite qua API
async function loadProducts(category = "all") {
  const grid = document.getElementById("productsGrid");
  const countLabel = document.getElementById("productCount");
  const sectionTitle = document.getElementById("sectionTitle");
  
  try {
    const url = category === "all" ? "/api/products" : `/api/search?q=${encodeURIComponent(category)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error("Server phản hồi chậm hoặc đang bị nghẽn mạng!");
    const data = await res.json();
    allProducts = data.products || [];
    
    if (category !== "all") {
      sectionTitle.textContent = `Danh mục: ${category}`;
    } else {
      sectionTitle.textContent = "Tất cả sản phẩm";
    }
    
    countLabel.textContent = `${allProducts.length} sản phẩm khả dụng`;
    renderProducts(allProducts);
  } catch (err) {
    grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: #ef4444; padding: 40px;">
      <h3>⚠️ Server đang bị DDoS chiếm trọn luồng xử lý!</h3>
      <p>Yêu cầu truy vấn SQLite bị tắc nghẽn (${err.message})</p>
    </div>`;
    countLabel.textContent = "Lỗi kết nối";
  }
}

// Render danh sách sản phẩm
function renderProducts(products) {
  const grid = document.getElementById("productsGrid");
  if (!products || products.length === 0) {
    grid.innerHTML = `<p style="grid-column: 1/-1; text-align: center; color: #64748b;">Không tìm thấy sản phẩm nào.</p>`;
    return;
  }

  grid.innerHTML = products.map(p => `
    <article class="product-card">
      <div class="product-card-top">
        <span>${p.image_icon}</span>
        <span class="card-category-badge">${p.category}</span>
      </div>
      <div class="product-card-body">
        <h3 class="product-name">${p.name}</h3>
        <p class="product-desc">${p.description}</p>
        <div class="product-meta">
          <span class="product-price">${formatVND(p.price)}</span>
          <span class="product-stock" id="stock-${p.id}">Kho: <b>${p.stock}</b></span>
        </div>
        <button class="btn-add-cart" id="btn-add-${p.id}" onclick="addToCart(${p.id}, this)">
          ${p.stock > 0 ? '🛒 Thêm vào giỏ' : '❌ Hết hàng'}
        </button>
      </div>
    </article>
  `).join("");
}

// 2. Tìm kiếm sản phẩm trực tiếp từ SQLite
async function searchProducts(keyword) {
  const grid = document.getElementById("productsGrid");
  const countLabel = document.getElementById("productCount");
  const sectionTitle = document.getElementById("sectionTitle");

  sectionTitle.textContent = `Kết quả tìm kiếm cho: "${keyword}"`;
  countLabel.textContent = "Đang truy vấn SQLite...";

  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(keyword)}`);
    const data = await res.json();
    const products = data.products || [];
    countLabel.textContent = `${products.length} sản phẩm`;

    if (products.length === 0) {
      grid.innerHTML = `<p style="grid-column: 1/-1; text-align: center; color: #64748b; padding: 40px;">Không tìm thấy sản phẩm nào khớp với từ khóa.</p>`;
      return;
    }

    grid.innerHTML = products.map(p => `
      <article class="product-card">
        <div class="product-card-top">
          <span>${p.image_icon}</span>
          <span class="card-category-badge">${p.category}</span>
        </div>
        <div class="product-card-body">
          <h3 class="product-name">${p.name}</h3>
          <p class="product-desc">${p.description}</p>
          <div class="product-meta">
            <span class="product-price">${formatVND(p.price)}</span>
            <span class="product-stock">Kho: ${p.stock}</span>
          </div>
          <button class="btn-add-cart" onclick="addToCart(${p.id})">🛒 Thêm vào giỏ</button>
        </div>
      </article>
    `).join("");
  } catch (err) {
    showToast("Tìm kiếm thất bại: Server đang phản hồi quá chậm!", "error");
  }
}

// 3. Kiểm tra trạng thái đăng nhập
async function checkAuthStatus() {
  const authSection = document.getElementById("authSection");
  try {
    const res = await fetch("/api/me");
    const data = await res.json();
    if (data.authenticated && data.user) {
      currentUser = data.user;
      authSection.innerHTML = `
        <div class="user-logged">
          <span>Xin chào, <b>${currentUser.fullname || currentUser.username}</b></span>
          <button class="btn-logout" onclick="logout()">Đăng xuất</button>
        </div>
      `;
    } else {
      currentUser = null;
      authSection.innerHTML = `
        <button class="btn-auth" id="openAuthModalBtn" onclick="openAuthModal()">
          <span class="icon">👤</span>
          <span>Đăng nhập / Đăng ký</span>
        </button>
      `;
    }
  } catch (e) {
    // Không kết nối được
  }
}

// Đăng nhập
async function handleLogin(e) {
  e.preventDefault();
  const username = document.getElementById("loginUsername").value;
  const password = document.getElementById("loginPassword").value;

  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: jsonPayload({ username, password })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(data.message, "success");
      closeAuthModal();
      checkAuthStatus();
    } else {
      showToast(data.message || "Đăng nhập thất bại!", "error");
    }
  } catch (err) {
    showToast("Không thể gửi dữ liệu: Server đang bị nghẽn!", "error");
  }
}

// Đăng ký tài khoản mới vào SQLite
async function handleRegister(e) {
  e.preventDefault();
  const username = document.getElementById("regUsername").value;
  const fullname = document.getElementById("regFullname").value;
  const email = document.getElementById("regEmail").value;
  const password = document.getElementById("regPassword").value;

  try {
    const res = await fetch("/api/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: jsonPayload({ username, fullname, email, password })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast("Tạo tài khoản thành công! Hãy đăng nhập.", "success");
      switchAuthTab("login");
      document.getElementById("loginUsername").value = username;
    } else {
      showToast(data.message || "Đăng ký thất bại!", "error");
    }
  } catch (err) {
    showToast("Lỗi kết nối tới cơ sở dữ liệu!", "error");
  }
}

// Đăng xuất
async function logout() {
  await fetch("/api/logout", { method: "POST" });
  showToast("Đã đăng xuất.", "success");
  checkAuthStatus();
}

function jsonPayload(obj) {
  return JSON.stringify(obj);
}

// 4. Giỏ hàng lưu trữ và đồng bộ với SQLite
async function loadCart() {
  try {
    const res = await fetch("/api/cart");
    if (!res.ok) return;
    const data = await res.json();
    cart = data.items || [];
    renderCartUI(data.total_price, data.total_count);
  } catch (err) {
    // Nghẽn mạng
  }
}

async function addToCart(productId, btnElement) {
  const origText = btnElement ? btnElement.innerHTML : "🛒 Thêm vào giỏ";
  if (btnElement) {
    btnElement.innerHTML = "Đang lưu kho... ⏳";
    btnElement.disabled = true;
  }

  try {
    const res = await fetch("/api/cart/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_id: productId, quantity: 1 })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      cart = data.items || [];
      renderCartUI(data.total_price, data.total_count);
      showToast("Đã lưu sản phẩm vào Giỏ Hàng SQLite!", "success");
    } else {
      showToast(data.message || "Không thể thêm sản phẩm!", "error");
    }
  } catch (err) {
    showToast("⚠️ Thao tác bị đơ / chậm: Server đang bị DoS quá tải!", "error");
  } finally {
    if (btnElement) {
      btnElement.innerHTML = origText;
      btnElement.disabled = false;
    }
  }
}

async function removeFromCart(productId) {
  try {
    const res = await fetch("/api/cart/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_id: productId })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      cart = data.items || [];
      renderCartUI(data.total_price, data.total_count);
      showToast("Đã xóa sản phẩm khỏi giỏ hàng SQLite.", "success");
    }
  } catch (err) {
    showToast("Server phản hồi quá chậm để xóa sản phẩm!", "error");
  }
}

async function checkout(btnElement) {
  if (cart.length === 0) return showToast("Giỏ hàng đang trống!", "error");
  
  const origText = btnElement ? btnElement.innerHTML : "Tiến hành đặt hàng";
  if (btnElement) {
    btnElement.innerHTML = "Đang trừ kho SQLite... ⏳";
    btnElement.disabled = true;
  }

  try {
    const res = await fetch("/api/cart/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(data.message, "success");
      cart = [];
      renderCartUI(0, 0);
      document.getElementById("cartDrawer").classList.remove("open");
      // Tải lại danh sách sản phẩm để cập nhật số lượng tồn kho mới từ SQLite
      loadProducts();
    } else {
      showToast(data.message || "Đặt hàng thất bại!", "error");
    }
  } catch (err) {
    showToast("⚠️ Giao dịch bị nghẽn: Server không kịp trừ kho SQLite do DDoS!", "error");
  } finally {
    if (btnElement) {
      btnElement.innerHTML = origText;
      btnElement.disabled = false;
    }
  }
}

function renderCartUI(totalPrice = 0, totalCount = 0) {
  document.getElementById("cartCount").textContent = totalCount || cart.reduce((s, i) => s + i.quantity, 0);
  document.getElementById("cartItemCount").textContent = totalCount || cart.length;
  
  const cartList = document.getElementById("cartList");
  if (!cart || cart.length === 0) {
    cartList.innerHTML = `<p class="empty-cart-msg">Giỏ hàng của bạn đang trống.</p>`;
    document.getElementById("cartTotalPrice").textContent = "0 đ";
    return;
  }

  const finalTotal = totalPrice || cart.reduce((sum, item) => sum + item.price * item.quantity, 0);
  document.getElementById("cartTotalPrice").textContent = formatVND(finalTotal);

  cartList.innerHTML = cart.map(item => `
    <div class="cart-item">
      <div class="cart-item-info">
        <strong>${item.name}</strong>
        <small>${item.category} (SL: ${item.quantity}) · Kho: ${item.stock}</small>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span class="cart-item-price">${formatVND(item.price * item.quantity)}</span>
        <button style="background:none; border:none; color:#ef4444; cursor:pointer; font-size:1rem;" title="Xóa" onclick="removeFromCart(${item.product_id})">🗑️</button>
      </div>
    </div>
  `).join("");
}

// 5. Quản lý Phòng thủ WAF và Giám sát Tải Thực tế
async function toggleDefense() {
  const btn = document.getElementById("toggleDefenseBtn");
  btn.disabled = true;
  try {
    const res = await fetch("/api/toggle-defense", { method: "POST" });
    const data = await res.json();
    updateDefenseStatus(data.defense_enabled);
    showToast(`Đã ${data.defense_enabled ? "BẬT" : "TẮT"} chế độ phòng thủ WAF!`, "success");
  } catch (err) {
    showToast("Server không phản hồi yêu cầu bật phòng thủ!", "error");
  } finally {
    btn.disabled = false;
  }
}

function updateDefenseStatus(enabled) {
  const dot = document.getElementById("defenseDot");
  const text = document.getElementById("defenseText");
  const btn = document.getElementById("toggleDefenseBtn");

  if (enabled) {
    dot.className = "status-indicator active";
    text.textContent = "ĐANG BẬT (Bảo Vệ Chủ Động)";
    text.style.color = "#4ade80";
    btn.textContent = "Tắt Phòng Thủ WAF";
    btn.className = "btn-toggle-defense active";
  } else {
    dot.className = "status-indicator";
    text.textContent = "ĐANG TẮT (Dễ bị sập)";
    text.style.color = "#f87171";
    btn.textContent = "Bật Phòng Thủ WAF";
    btn.className = "btn-toggle-defense";
  }
}

// Giám sát trạng thái server & đo độ trễ thực tế
function startServerMonitoring() {
  setInterval(async () => {
    const start = performance.now();
    const latencyDot = document.querySelector(".ping-dot");
    const latencyText = document.getElementById("latencyText");

    try {
      const res = await fetch("/api/server-status");
      const elapsed = Math.round(performance.now() - start);

      if (res.ok) {
        const data = await res.json();
        updateDefenseStatus(data.defense_enabled);
        document.getElementById("workerStats").textContent = `${data.active_workers} / ${data.max_workers} (${data.worker_utilization_pct}%)`;
        document.getElementById("totalReqStats").textContent = data.total_requests;
        document.getElementById("blockedReqStats").textContent = data.blocked_requests;
        if (document.getElementById("cpuStats")) {
          document.getElementById("cpuStats").textContent = data.cpu_load || "1%";
          document.getElementById("cpuStats").style.color = data.cpu_load?.includes("100%") ? "#ef4444" : "#f59e0b";
        }
        if (document.getElementById("ramStats")) {
          document.getElementById("ramStats").textContent = `${data.ram_consumed_mb || 0} MB`;
        }

        if (elapsed > 400 || data.worker_utilization_pct >= 85) {
          latencyDot.className = "ping-dot lag";
          latencyText.textContent = `Ping: ${elapsed}ms (NGHẼN MẠNG / QUÁ TẢI)`;
          latencyText.style.color = "#f87171";
        } else {
          latencyDot.className = "ping-dot";
          latencyText.textContent = `Ping: ${elapsed}ms (Mượt)`;
          latencyText.style.color = "#f1f5f9";
        }
      }
    } catch (err) {
      latencyDot.className = "ping-dot lag";
      latencyText.textContent = "MẤT KẾT NỐI (SERVER BỊ DDOS)";
      latencyText.style.color = "#ef4444";
    }
  }, 1500);
}

// 6. UI Helpers & Event Listeners
function setupEventListeners() {
  // Tabs danh mục
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      loadProducts(btn.dataset.cat);
    });
  });

  // Tìm kiếm
  document.getElementById("searchForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = document.getElementById("searchInput").value.trim();
    if (q) searchProducts(q);
    else loadProducts();
  });

  // Modal Auth
  document.getElementById("closeAuthModalBtn").addEventListener("click", closeAuthModal);
  document.getElementById("tabLogin").addEventListener("click", () => switchAuthTab("login"));
  document.getElementById("tabRegister").addEventListener("click", () => switchAuthTab("register"));
  document.getElementById("loginForm").addEventListener("submit", handleLogin);
  document.getElementById("registerForm").addEventListener("submit", handleRegister);

  // Cart Drawer
  document.getElementById("openCartBtn").addEventListener("click", () => {
    document.getElementById("cartDrawer").classList.add("open");
  });
  document.getElementById("closeCartBtn").addEventListener("click", () => {
    document.getElementById("cartDrawer").classList.remove("open");
  });
  document.getElementById("checkoutBtn").addEventListener("click", function() {
    checkout(this);
  });

  // Nút WAF
  document.getElementById("toggleDefenseBtn").addEventListener("click", toggleDefense);
}

function openAuthModal() {
  document.getElementById("authModal").classList.add("open");
}

function closeAuthModal() {
  document.getElementById("authModal").classList.remove("open");
}

function switchAuthTab(tab) {
  const isLogin = tab === "login";
  document.getElementById("tabLogin").classList.toggle("active", isLogin);
  document.getElementById("tabRegister").classList.toggle("active", !isLogin);
  document.getElementById("loginForm").style.display = isLogin ? "block" : "none";
  document.getElementById("registerForm").style.display = isLogin ? "none" : "block";
}

function showToast(message, type = "success") {
  const container = document.getElementById("toastContainer");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}
