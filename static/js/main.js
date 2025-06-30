
function handleAddToWishlist(button) {
  const card = button.closest('.card');
  const name = card.querySelector('.card-title')?.innerText;
  const image = card.querySelector('img')?.getAttribute('src'); // ✅ Use .src to get full absolute URL
  const details = card.querySelector('p')?.innerText;

  if (!name || !image || !details) {
    alert("Missing item information.");
    return;
  }
  const filename = image.split('/').pop(); // ← only gets "photo-1493558103817-58b2924bce98"
const staticPath = `/static/images/${filename}`; // ← assumes local static path


  fetch('/virtual_exhibition', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: name,
      details: details,
      image: staticPath // ✅ Send full image URL directly
    })
  })
    .then(res => res.json())
    .then(data => {
      alert(data.message || "Item added to wishlist.");
    });
}

function fetchWishlist() {
  if (window.location.pathname !== '/wishlist') return;

  fetch('/wishlist_data')
    .then(res => res.json())
    .then(data => {
      const container = document.getElementById('wishlist-items');
      if (!data.wishlist.length) {
        container.innerHTML = '<p class="text-center">No items in your wishlist.</p>';
        return;
      }

      container.innerHTML = ''; // Clear old content

      data.wishlist.forEach(item => {
  const row = document.createElement('div');
  row.className = 'wishlist-row d-flex align-items-center justify-content-between p-3 mb-3 border rounded';

  row.innerHTML = `
    <div>
      <h5 class="mb-1">${item.item_name}</h5>
      <p class="mb-1">${item.details.replace(/\n/g, '<br>')}</p>
    </div>
    <div>
      <button class="btn btn-outline-danger btn-sm me-2" onclick="removeFromWishlist('${item.item_id}')">
        <i class="fas fa-trash me-1"></i> Remove
      </button>
      <button class="btn btn-primary btn-sm" onclick="addToCheckout('${item.item_id}')">
        <i class="fas fa-cart-plus me-1"></i> Checkout
      </button>
    </div>
  `;
  container.appendChild(row);
});

    });
}

function removeFromWishlist(item_id) {
  fetch('/remove_from_wishlist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_id })
  }).then(() => fetchWishlist());
}

function addToCheckout(item_id) {
  fetch('/add_to_checkout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_id })
  }).then(res => res.json()).then(data => {
    if (data.redirect) {
      window.location.href = data.redirect;
    } else {
      alert(data.message || "Item added to checkout.");
    }
  });
}


if (window.location.pathname === '/wishlist') {
  fetchWishlist();
}

