/* Tide Chart — gTrade Trading Integration
 * Wallet connection, chain switching, and trade execution
 * via Gains Network on Arbitrum One.
 */

/* ========== Chainlink Price Feed Addresses (Arbitrum One) ========== */
var CHAINLINK_FEEDS = {
  'BTC': '0x6ce185860a4963106506C203335A2910413708e9',
  'ETH': '0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612',
  'SOL': '0x24ceA4b8ce57cdA5058b924B9B9987992450590c',
  'DOGE': '0x9A7FB1b3950837a8D9b40517626E11D4127C098C',
  'AAPL': '0xc4A750B3E14bEF69Db22F2f5AaEEb77b6d1A4E42',
  'TSLA': '0x3609baAa0a9b1F0FE4B300b15BCa8bBdB8C22E66',
  'AMZN': '0xd6a77691f071E98Df7217BED98f38ae6d2313EBA',
  'GOOGL': '0x1D1a83331e9D255EB1Aaf75026B60dFD00A252ba',
  'META': '0xcd1BD86FDc33080DCF1b5715B6FCe04eC6F85845',
  'NVDA': '0x4881A4418b5F2460B21d6F08CD5aA0678a7f262F',
  'SPY': '0x46306F3795342117721D8DEd50fbcE4eFbee0aBe',
  'XAU': '0x1F954Dc24a49708C26E0C1777f16750B5C6d5a2c'
};

/* Cached mapping from pairIndex -> asset ticker, populated by loadOpenTrades */
var pairIndexToTicker = {};

/* Cache of open trade metadata keyed by tradeIndex, used to record history on close */
var _openTradesCache = {};
var TRADE_HISTORY_KEY = 'tidechart_trade_history';

function getTradeHistory() {
  try {
    var raw = localStorage.getItem(TRADE_HISTORY_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (_) { return []; }
}

function saveTradeToHistory(entry) {
  var history = getTradeHistory();
  history.unshift(entry);
  if (history.length > 50) history = history.slice(0, 50);
  try { localStorage.setItem(TRADE_HISTORY_KEY, JSON.stringify(history)); } catch (_) {}
}

function resolveFeedForPairIndex(pairIndex, pairNames) {
  if (pairIndexToTicker[pairIndex]) return CHAINLINK_FEEDS[pairIndexToTicker[pairIndex]] || null;
  var name = pairNames[pairIndex];
  if (!name) return null;
  var ticker = name.split('/')[0];
  if (ticker) pairIndexToTicker[pairIndex] = ticker;
  return CHAINLINK_FEEDS[ticker] || null;
}

async function fetchChainlinkPrice(feedAddr, provider) {
  if (!feedAddr || !provider) return null;
  try {
    var feedAbi = ['function latestRoundData() view returns (uint80,int256,uint256,uint256,uint80)'];
    var feed = new ethers.Contract(feedAddr, feedAbi, provider);
    var roundData = await feed.latestRoundData();
    return Number(roundData[1]) / 1e8;
  } catch (_) { return null; }
}

var walletState = {
  connected: false,
  address: null,
  provider: null,
  signer: null,
  chainId: null,
  usdcBalance: '0'
};

var gtradeConfig = null;
var tradePending = false;

function shortAddr(addr) {
  return addr ? addr.slice(0, 6) + '...' + addr.slice(-4) : '';
}

/* ========== Toast Notification System ========== */
function showToast(message, type, duration) {
  type = type || 'info';
  duration = duration || 5000;
  var container = document.getElementById('toast-container');
  if (!container) return;
  var toast = document.createElement('div');
  toast.className = 'toast ' + type;
  var icons = { success: '\u2713', error: '\u2717', info: '\u2139' };
  toast.innerHTML = '<span class="toast-icon">' + (icons[type] || icons.info) + '</span>'
    + '<span class="toast-msg">' + message + '</span>';
  container.appendChild(toast);
  setTimeout(function() {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(function() { toast.remove(); }, 300);
  }, duration);
}

function showTradeStatus(msg, isError, isHtml) {
  var el = document.getElementById('trade-status');
  if (!el) return;
  if (isHtml) { el.innerHTML = msg; } else { el.textContent = msg; }
  el.className = 'trade-status ' + (isError ? 'error' : 'success');
  el.style.display = 'block';
}

function hideTradeStatus() {
  var el = document.getElementById('trade-status');
  if (el) el.style.display = 'none';
  var fb = document.getElementById('trade-fallback');
  if (fb) fb.style.display = 'none';
}

/* ========== Client-Side Trade Validation (gTrade Protocol Guards) ========== */
function getAssetLimits(asset) {
  if (!gtradeConfig || !gtradeConfig.pairs || !gtradeConfig.group_limits) return null;
  var pair = gtradeConfig.pairs[asset];
  if (!pair) return null;
  return gtradeConfig.group_limits[pair.group] || null;
}

function validateTradeClient() {
  var execBtn = document.getElementById('trade-exec-btn');
  var posEl = document.getElementById('trade-pos-size');
  if (!execBtn) return;

  var asset = (document.getElementById('trade-asset') || {}).value || '';
  var leverage = parseFloat((document.getElementById('trade-leverage') || {}).value) || 0;
  var collateral = parseFloat((document.getElementById('trade-collateral') || {}).value) || 0;

  var limits = getAssetLimits(asset);
  var minCol = (gtradeConfig && gtradeConfig.collateral_limits) ? gtradeConfig.collateral_limits.min_usd : 5;

  var positionSize = collateral * leverage;

  // Update position size display
  if (posEl) {
    var fmt = positionSize.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    posEl.textContent = '$' + fmt;
    posEl.className = 'trade-pos-size' + (limits && positionSize > 0 && positionSize < limits.min_position_usd ? ' warning' : '');
  }

  // Guard: no wallet
  // Reset direction class
  execBtn.className = 'trade-exec-btn';

  if (!walletState.connected) {
    execBtn.disabled = true;
    execBtn.textContent = 'Connect Wallet';
    return;
  }

  // Guard: wrong chain
  if (walletState.chainId !== 42161) {
    execBtn.disabled = true;
    execBtn.textContent = 'Switch to Arbitrum';
    return;
  }

  // Guard: no asset
  if (!asset || !limits) {
    execBtn.disabled = true;
    execBtn.textContent = 'Select an Asset';
    return;
  }

  // Guard: collateral empty
  if (collateral <= 0) {
    execBtn.disabled = true;
    execBtn.textContent = 'Enter Collateral';
    return;
  }

  // Guard: collateral below minimum
  if (collateral < minCol) {
    execBtn.disabled = true;
    execBtn.textContent = 'Min Collateral: $' + minCol;
    return;
  }

  // Guard: collateral above maximum
  if (collateral > limits.max_collateral_usd) {
    execBtn.disabled = true;
    execBtn.textContent = 'Max Collateral: $' + limits.max_collateral_usd.toLocaleString();
    return;
  }

  // Guard: leverage out of range
  if (leverage < limits.min_leverage) {
    execBtn.disabled = true;
    execBtn.textContent = 'Min Leverage: ' + limits.min_leverage + 'x';
    return;
  }
  if (leverage > limits.max_leverage) {
    execBtn.disabled = true;
    execBtn.textContent = 'Max Leverage: ' + limits.max_leverage + 'x';
    return;
  }

  // Guard: position size below protocol minimum
  if (positionSize < limits.min_position_usd) {
    execBtn.disabled = true;
    execBtn.textContent = 'Min Position: $' + limits.min_position_usd.toLocaleString();
    return;
  }

  // Guard: insufficient USDC balance
  var usdcBal = parseFloat(walletState.usdcBalance) || 0;
  if (usdcBal < collateral) {
    execBtn.disabled = true;
    execBtn.textContent = 'Insufficient USDC (have: $' + usdcBal.toFixed(2) + ')';
    return;
  }

  // Guard: trade in progress
  if (tradePending) {
    execBtn.disabled = true;
    execBtn.textContent = 'Processing...';
    return;
  }

  // All guards pass — enable button with direction-aware styling
  var direction = (document.getElementById('trade-direction') || {}).value || 'long';
  execBtn.disabled = false;
  execBtn.className = 'trade-exec-btn ' + direction;
  execBtn.textContent = 'Open ' + direction.charAt(0).toUpperCase() + direction.slice(1) + ' ' + asset;
}

function updateWalletUI() {
  var btn = document.getElementById('wallet-btn');
  var info = document.getElementById('wallet-info');
  var balEl = document.getElementById('usdc-balance');
  var tradeSection = document.getElementById('trade-form-section');
  var chainBadge = document.getElementById('chain-badge');

  if (walletState.connected) {
    btn.textContent = shortAddr(walletState.address);
    btn.classList.add('connected');
    if (info) info.style.display = 'flex';
    if (balEl) balEl.textContent = parseFloat(walletState.usdcBalance).toFixed(2);
    if (tradeSection) tradeSection.style.display = 'block';
    if (chainBadge) {
      if (walletState.chainId === 42161) {
        chainBadge.textContent = 'Arbitrum';
        chainBadge.className = 'chain-badge arb-ok';
      } else {
        chainBadge.textContent = 'Wrong Chain';
        chainBadge.className = 'chain-badge arb-wrong';
      }
      chainBadge.style.display = 'inline-block';
    }
  } else {
    btn.textContent = 'Connect Wallet';
    btn.classList.remove('connected');
    if (info) info.style.display = 'none';
    if (chainBadge) chainBadge.style.display = 'none';
  }
}

async function connectWallet() {
  if (walletState.connected) {
    walletState = { connected: false, address: null, provider: null, signer: null, chainId: null, usdcBalance: '0' };
    updateWalletUI();
    hideTradeStatus();
    return;
  }

  if (typeof window.ethereum === 'undefined') {
    showToast('No wallet detected. Install <a href="https://metamask.io" target="_blank">MetaMask</a> or any EIP-1193 wallet.', 'error', 8000);
    return;
  }

  try {
    var provider = new ethers.BrowserProvider(window.ethereum);
    await provider.send('eth_requestAccounts', []);
    var signer = await provider.getSigner();
    var address = await signer.getAddress();
    var network = await provider.getNetwork();

    walletState.connected = true;
    walletState.address = address;
    walletState.provider = provider;
    walletState.signer = signer;
    walletState.chainId = Number(network.chainId);

    updateWalletUI();

    if (walletState.chainId !== 42161) {
      await switchToArbitrum();
    }

    await refreshUSDCBalance();
    loadOpenTrades();
    loadTradeHistory();
    showToast('Connected: ' + shortAddr(address), 'success');
    validateTradeClient();
  } catch (e) {
    if (e.code === 4001 || (e.info && e.info.error && e.info.error.code === 4001)) {
      showToast('Connection rejected by user', 'error');
    } else {
      showToast('Connection failed: ' + (e.message || String(e)), 'error');
    }
  }
}

async function switchToArbitrum() {
  if (!window.ethereum) return;
  try {
    await window.ethereum.request({
      method: 'wallet_switchEthereumChain',
      params: [{ chainId: '0xa4b1' }]
    });
  } catch (err) {
    if (err.code === 4902) {
      await window.ethereum.request({
        method: 'wallet_addEthereumChain',
        params: [{
          chainId: '0xa4b1',
          chainName: 'Arbitrum One',
          rpcUrls: ['https://arb1.arbitrum.io/rpc'],
          blockExplorerUrls: ['https://arbiscan.io'],
          nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 }
        }]
      });
    } else {
      throw err;
    }
  }
  walletState.provider = new ethers.BrowserProvider(window.ethereum);
  walletState.signer = await walletState.provider.getSigner();
  var net = await walletState.provider.getNetwork();
  walletState.chainId = Number(net.chainId);
  updateWalletUI();
}

async function refreshUSDCBalance() {
  if (!walletState.connected || !gtradeConfig) return;
  try {
    var erc20Abi = ['function balanceOf(address) view returns (uint256)'];
    var usdc = new ethers.Contract(gtradeConfig.usdc_contract, erc20Abi, walletState.provider);
    var raw = await usdc.balanceOf(walletState.address);
    walletState.usdcBalance = ethers.formatUnits(raw, gtradeConfig.usdc_decimals);
    updateWalletUI();
  } catch (e) {
    console.warn('Could not fetch USDC balance:', e.message);
  }
}

function populateTradeAssets() {
  var sel = document.getElementById('trade-asset');
  if (!sel || !gtradeConfig) return;
  sel.innerHTML = '';
  Object.keys(gtradeConfig.pairs).forEach(function(asset) {
    var opt = document.createElement('option');
    opt.value = asset;
    var price = (typeof currentAssets !== 'undefined' && currentAssets[asset])
      ? ' ($' + currentAssets[asset].current_price.toFixed(2) + ')'
      : '';
    opt.textContent = gtradeConfig.pairs[asset].name + price;
    sel.appendChild(opt);
  });
}

function updateTradePreview() {
  // Run client-side guards on every input change
  validateTradeClient();

  var assetEl = document.getElementById('trade-asset');
  var dirEl = document.getElementById('trade-direction');
  var levEl = document.getElementById('trade-leverage');
  var colEl = document.getElementById('trade-collateral');
  var preview = document.getElementById('trade-preview');
  if (!assetEl || !preview) return;

  var asset = assetEl.value;
  var direction = dirEl.value;
  var leverage = parseFloat(levEl.value) || 0;
  var collateral = parseFloat(colEl.value) || 0;

  if (!asset || leverage <= 0 || collateral <= 0) {
    preview.style.display = 'none';
    return;
  }

  var posSize = collateral * leverage;
  var price = (typeof currentAssets !== 'undefined' && currentAssets[asset])
    ? currentAssets[asset].current_price : 0;
  var fmt = function(n) { return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); };

  preview.style.display = 'block';
  var html =
    '<div class="preview-row"><span>Position Size</span><span>$' + fmt(posSize) + '</span></div>' +
    '<div class="preview-row"><span>Direction</span><span class="' +
      (direction === 'long' ? 'positive' : 'negative') + '">' +
      direction.toUpperCase() + ' ' + leverage + 'x</span></div>' +
    '<div class="preview-row"><span>Entry Price</span><span>$' + fmt(price) + ' (market)</span></div>' +
    '<div class="preview-row"><span>Collateral</span><span>' + fmt(collateral) + ' USDC</span></div>';

  var tp = parseFloat(document.getElementById('trade-tp').value);
  var sl = parseFloat(document.getElementById('trade-sl').value);
  var slippage = parseFloat(document.getElementById('trade-slippage').value) || 1;
  if (tp > 0) html += '<div class="preview-row"><span>Take Profit</span><span class="positive">+' + tp + '%</span></div>';
  if (sl > 0) html += '<div class="preview-row"><span>Stop Loss</span><span class="negative">-' + sl + '%</span></div>';
  html += '<div class="preview-row"><span>Max Slippage</span><span>' + slippage.toFixed(1) + '%</span></div>';
  html += '<div class="preview-row"><span>Protocol</span><span>gTrade &middot; Arbitrum</span></div>';
  preview.innerHTML = html;
}

async function executeTrade() {
  hideTradeStatus();
  var fb = document.getElementById('trade-fallback');
  if (fb) fb.style.display = 'none';
  if (tradePending) return;

  if (!walletState.connected) {
    showToast('Connect your wallet first', 'error');
    return;
  }

  if (walletState.chainId !== 42161) {
    showToast('Switching to Arbitrum...', 'info', 3000);
    try { await switchToArbitrum(); } catch (_) { /* user rejected */ }
    return;
  }

  var asset = document.getElementById('trade-asset').value;
  var direction = document.getElementById('trade-direction').value;
  var leverage = parseFloat(document.getElementById('trade-leverage').value);
  var collateral = parseFloat(document.getElementById('trade-collateral').value);
  var tpPct = parseFloat(document.getElementById('trade-tp').value) || 0;
  var slPct = parseFloat(document.getElementById('trade-sl').value) || 0;

  // Fetch live price from Chainlink on-chain feed (same oracle gTrade uses)
  var currentPrice = 0;
  var feedAddr = CHAINLINK_FEEDS[asset];
  if (feedAddr && walletState.provider) {
    var clPrice = await fetchChainlinkPrice(feedAddr, walletState.provider);
    if (clPrice) currentPrice = clPrice;
  }
  if (!currentPrice) {
    currentPrice = (typeof currentAssets !== 'undefined' && currentAssets[asset])
      ? currentAssets[asset].current_price : 0;
  }
  if (!currentPrice || currentPrice <= 0) {
    showToast('No market price available for ' + asset + '. Try again.', 'error');
    return;
  }
  // openPrice uses 1e10 precision on-chain
  var openPriceScaled = BigInt(Math.round(currentPrice * 1e10));

  // TP/SL as absolute prices in 1e10 precision
  var tpScaled = 0;
  var slScaled = 0;
  if (tpPct > 0) {
    tpScaled = BigInt(Math.round(currentPrice * (1 + tpPct / 100) * 1e10));
  }
  if (slPct > 0) {
    slScaled = BigInt(Math.round(currentPrice * (1 - slPct / 100) * 1e10));
  }

  // Server-side validation (mirrors client-side guards)
  var valResp = await fetch('/api/gtrade/validate-trade', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ asset: asset, direction: direction, leverage: leverage, collateral_usd: collateral })
  });
  var valData = await valResp.json();
  if (!valData.valid) {
    showToast(valData.error, 'error');
    return;
  }

  tradePending = true;
  var execBtn = document.getElementById('trade-exec-btn');
  var originalText = execBtn.textContent;
  execBtn.disabled = true;
  execBtn.textContent = 'Processing...';

  try {
    // USDC amounts: native 1e6 precision for both ERC-20 ops and trade struct
    var collateralAmount = ethers.parseUnits(collateral.toString(), gtradeConfig.usdc_decimals);

    // Check and approve USDC allowance (uses native USDC precision: 1e6)
    var erc20Abi = [
      'function allowance(address,address) view returns (uint256)',
      'function approve(address,uint256) returns (bool)'
    ];
    var usdc = new ethers.Contract(gtradeConfig.usdc_contract, erc20Abi, walletState.signer);
    var allowance = await usdc.allowance(walletState.address, gtradeConfig.trading_contract);

    if (allowance < collateralAmount) {
      showToast('Approving USDC spend...', 'info', 10000);
      execBtn.textContent = 'Approving USDC...';
      var approveTx = await usdc.approve(gtradeConfig.trading_contract, ethers.MaxUint256);
      await approveTx.wait();
      showToast('USDC approved', 'success', 3000);
    }

    // Resolve pair index from gTrade API
    var pairResp = await fetch('/api/gtrade/resolve-pair?asset=' + asset);
    var pairData = await pairResp.json();

    if (pairData.pair_index === null || pairData.pair_index === undefined) {
      showToast('Could not resolve gTrade pair index for ' + asset + '. Try again later.', 'error');
      showTradeFallback(asset);
      return;
    }

    // Build trade struct matching ITradingStorage.Trade on-chain
    // Verified selector: 0x5bfcc4f8 via https://api.openchain.xyz
    var tradeAbi = [
      'function openTrade(' +
        'tuple(address user, uint32 index, uint16 pairIndex, uint24 leverage, ' +
        'bool long, bool isOpen, uint8 collateralIndex, uint8 tradeType, ' +
        'uint120 collateralAmount, uint64 openPrice, uint64 tp, uint64 sl, ' +
        'bool isCounterTrade, uint160 positionSizeToken, uint24 __placeholder) _trade, ' +
        'uint16 _maxSlippageP, address _referrer)'
    ];

    var diamond = new ethers.Contract(gtradeConfig.trading_contract, tradeAbi, walletState.signer);
    var leverageScaled = Math.round(leverage * 1000);

    // slippageP uses 1e3 precision: 1% = 1000, 0.5% = 500
    var slippageInput = parseFloat(document.getElementById('trade-slippage').value) || 1.5;
    var slippageP = Math.round(slippageInput * 1000);

    var trade = {
      user: walletState.address,
      index: 0,                    // trade counter (0 for new trade)
      pairIndex: pairData.pair_index,
      leverage: leverageScaled,
      long: direction === 'long',
      isOpen: false,
      collateralIndex: gtradeConfig.collateral_index,
      tradeType: 0,                // 0 = market order
      collateralAmount: collateralAmount,
      openPrice: openPriceScaled,   // current market price in 1e10
      tp: tpScaled,
      sl: slScaled,
      isCounterTrade: false,
      positionSizeToken: 0,
      __placeholder: 0
    };

    showToast('Opening ' + direction + ' ' + asset + '...', 'info', 15000);
    execBtn.textContent = 'Opening Trade...';

    var tx = await diamond.openTrade(trade, slippageP, ethers.ZeroAddress, { gasLimit: 3000000 });
    showToast('Transaction submitted. Waiting for confirmation...', 'info', 20000);
    var receipt = await tx.wait();

    showToast(
      'Trade opened! <a href="https://arbiscan.io/tx/' + receipt.hash + '" target="_blank" rel="noopener">View on Arbiscan</a>',
      'success', 10000
    );
    await refreshUSDCBalance();
    // Poll for backend to index the new trade (oracle callback is async)
    pollOpenTrades(5, 3000);

  } catch (e) {
    var msg = e.reason || e.shortMessage || e.message || 'Transaction failed';
    if (e.code === 4001 || (e.info && e.info.error && e.info.error.code === 4001)) {
      msg = 'Transaction rejected by user';
    } else if (msg.toLowerCase().indexOf('insufficient') !== -1) {
      msg = 'Insufficient funds (check USDC balance and ETH for gas)';
    } else if (msg.toLowerCase().indexOf('market') !== -1 || msg.toLowerCase().indexOf('closed') !== -1) {
      msg = 'Market may be closed. Equity markets trade during US hours.';
    }
    if (msg.length > 150) msg = msg.slice(0, 150) + '...';
    showToast(msg, 'error', 8000);
    showTradeFallback(asset);
  } finally {
    tradePending = false;
    execBtn.textContent = originalText;
    validateTradeClient();
  }
}

function showTradeFallback(asset) {
  var el = document.getElementById('trade-fallback');
  if (el) {
    el.style.display = 'block';
    var link = el.querySelector('a');
    if (link) link.href = 'https://gains.trade/trading/' + asset + '-USD';
  }
}

function selectTradeAsset(asset) {
  var sel = document.getElementById('trade-asset');
  if (sel) {
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].value === asset) { sel.selectedIndex = i; break; }
    }
  }
  var section = document.getElementById('trade-form-section');
  if (section) {
    section.style.display = 'block';
    section.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  updateTradePreview();
}

function pollOpenTrades(attempts, intervalMs) {
  var count = 0;
  loadOpenTrades();
  loadTradeHistory();
  var timer = setInterval(function() {
    count++;
    loadOpenTrades();
    loadTradeHistory();
    refreshUSDCBalance();
    if (count >= attempts) clearInterval(timer);
  }, intervalMs);
}

async function loadOpenTrades() {
  if (!walletState.connected) return;
  var container = document.getElementById('open-trades-list');
  if (!container) return;
  try {
    var resp = await fetch('/api/gtrade/open-trades?address=' + walletState.address);
    var data = await resp.json();
    var trades = data.trades || [];
    var pairNames = data.pair_names || {};
    if (trades.length === 0) {
      container.innerHTML = '<div class="no-trades">No open positions</div>';
      return;
    }

    // Populate pairIndexToTicker cache from pair_names
    Object.keys(pairNames).forEach(function(idx) {
      var ticker = pairNames[idx].split('/')[0];
      if (ticker) pairIndexToTicker[idx] = ticker;
    });

    // Fetch live Chainlink prices for each unique pair index
    var uniquePairs = {};
    trades.forEach(function(item) {
      var t = item.trade || item;
      var pairIdx = parseInt(t.pairIndex || '0');
      if (!uniquePairs[pairIdx]) uniquePairs[pairIdx] = true;
    });
    var livePrices = {};
    if (walletState.provider) {
      var pricePromises = Object.keys(uniquePairs).map(async function(pairIdx) {
        var feedAddr = resolveFeedForPairIndex(parseInt(pairIdx), pairNames);
        if (feedAddr) {
          var price = await fetchChainlinkPrice(feedAddr, walletState.provider);
          if (price) livePrices[pairIdx] = price;
        }
      });
      await Promise.all(pricePromises);
    }
    // Fallback: fill missing prices from Synth API currentAssets cache
    if (typeof currentAssets !== 'undefined') {
      Object.keys(uniquePairs).forEach(function(pairIdx) {
        if (!livePrices[pairIdx]) {
          var ticker = pairIndexToTicker[pairIdx];
          if (ticker && currentAssets[ticker] && currentAssets[ticker].current_price) {
            livePrices[pairIdx] = currentAssets[ticker].current_price;
          }
        }
      });
    }

    _openTradesCache = {};
    var html = '';
    trades.forEach(function(item) {
      var t = item.trade || item;
      var pairIdx = parseInt(t.pairIndex || '0');
      var tradeIdx = parseInt(t.index || '0');
      var dir = t.long ? 'LONG' : 'SHORT';
      var dirClass = t.long ? 'positive' : 'negative';
      var pairLabel = pairNames[pairIdx] || ('Pair #' + pairIdx);
      var lev = t.leverage ? (parseFloat(t.leverage) / 1000).toFixed(0) + 'x' : '?x';
      var levNum = t.leverage ? parseFloat(t.leverage) / 1000 : 0;
      // Cache for trade history recording
      _openTradesCache[tradeIdx] = { pairIdx: pairIdx, pairLabel: pairLabel, dir: dir, lev: lev, long: t.long,
        leverage: t.leverage, collateralAmount: t.collateralAmount, collateralIndex: t.collateralIndex, openPrice: t.openPrice };
      var colRaw = BigInt(t.collateralAmount || '0');
      var colIdx = parseInt(t.collateralIndex || '3');
      var colDecimals = (colIdx === 3) ? 6 : 18;
      var col = Number(colRaw) / Math.pow(10, colDecimals);
      var colFmt = col.toFixed(2);
      var entryPrice = t.openPrice ? parseFloat(t.openPrice) / 1e10 : 0;
      var entryFmt = entryPrice > 0 ? '$' + entryPrice.toFixed(2) : '?';

      // Calculate unrealized P&L
      var pnlHtml = '';
      var curPrice = livePrices[pairIdx];
      if (curPrice && entryPrice > 0 && levNum > 0) {
        var pnlPct = t.long
          ? ((curPrice - entryPrice) / entryPrice) * levNum * 100
          : ((entryPrice - curPrice) / entryPrice) * levNum * 100;
        var pnlUsd = col * (pnlPct / 100);
        var pnlClass = pnlUsd >= 0 ? 'positive' : 'negative';
        var pnlSign = pnlUsd >= 0 ? '+' : '';
        pnlHtml = '<span class="trade-pnl ' + pnlClass + '">' +
          pnlSign + pnlUsd.toFixed(2) + ' USDC (' + pnlSign + pnlPct.toFixed(2) + '%)' +
          '</span>';
      }

      html += '<div class="open-trade-row">' +
        '<div class="trade-row-info">' +
        '<div class="trade-row-main">' +
        '<span class="' + dirClass + '">' + dir + ' ' + lev + '</span>' +
        '<span>' + pairLabel + '</span>' +
        '<span>Entry: ' + entryFmt + (curPrice ? ' / Now: $' + curPrice.toFixed(2) : '') + '</span>' +
        '<span>' + colFmt + ' USDC</span>' +
        '</div>' +
        (pnlHtml ? '<div class="trade-row-pnl">' + pnlHtml + '</div>' : '') +
        '</div>' +
        '<button class="close-trade-btn" onclick="closeTrade(' + tradeIdx + ',' + pairIdx + ')" title="Close position">&#x2715;</button>' +
        '</div>';
    });
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<div class="no-trades">Could not load trades</div>';
  }
}

function loadTradeHistory() {
  var container = document.getElementById('trade-history-list');
  if (!container) return;
  var history = getTradeHistory();
  if (history.length === 0) {
    container.innerHTML = '<div class="no-trades">No trade history</div>';
    return;
  }
  var html = '';
  history.forEach(function(h) {
    var dirClass = h.long ? 'positive' : 'negative';
    var pnlVal = parseFloat(h.pnlUsd || '0');
    var pnlPctVal = parseFloat(h.pnlPct || '0');
    var pnlClass = pnlVal >= 0 ? 'positive' : 'negative';
    var pnlSign = pnlVal >= 0 ? '+' : '';
    var pnlHtml = '<span class="trade-pnl ' + pnlClass + '">' +
      pnlSign + pnlVal.toFixed(2) + ' USDC (' + pnlSign + pnlPctVal.toFixed(1) + '%)</span>';
    var txLink = h.txHash
      ? ' <a href="https://arbiscan.io/tx/' + h.txHash + '" target="_blank" rel="noopener" style="color:var(--accent);font-size:10px">tx</a>'
      : '';
    html += '<div class="open-trade-row history-row">' +
      '<div class="trade-row-info">' +
      '<div class="trade-row-main">' +
      '<span class="' + dirClass + '">' + (h.dir || '?') + ' ' + (h.lev || '?x') + '</span>' +
      '<span>' + (h.pairLabel || '?') + '</span>' +
      '<span>Entry: $' + (h.entryPrice || '?') + ' / Close: $' + (h.closePrice || '?') + '</span>' +
      '<span>' + (h.collateral || '?') + ' USDC</span>' +
      '</div>' +
      '<div class="trade-row-pnl">' + pnlHtml + txLink + '</div>' +
      '</div>' +
      '<span class="history-badge">CLOSED</span>' +
      '</div>';
  });
  container.innerHTML = html;
}

async function closeTrade(tradeIndex, pairIndex) {
  if (!walletState.connected || !walletState.signer) {
    showToast('Connect wallet first', 'error');
    return;
  }
  if (walletState.chainId !== 42161) {
    showToast('Switch to Arbitrum One', 'error');
    return;
  }
  if (tradePending) {
    showToast('Transaction already in progress', 'error');
    return;
  }
  tradePending = true;
  try {
    // Resolve Chainlink feed dynamically from cached pair names
    var feedAddr = resolveFeedForPairIndex(pairIndex, pairIndexToTicker);
    if (!feedAddr) {
      // Fallback: fetch pair names from server to populate the cache
      try {
        var prResp = await fetch('/api/gtrade/open-trades?address=' + walletState.address);
        var prData = await prResp.json();
        var pairNames = prData.pair_names || {};
        feedAddr = resolveFeedForPairIndex(pairIndex, pairNames);
      } catch (_) {}
    }
    var expectedPrice = BigInt(0);
    if (feedAddr && walletState.provider) {
      var livePrice = await fetchChainlinkPrice(feedAddr, walletState.provider);
      if (livePrice) {
        expectedPrice = BigInt(Math.round(livePrice * 1e10));
      }
    }
    // Fallback: use Synth API price from currentAssets cache (stocks, commodities, etc.)
    if (expectedPrice === BigInt(0)) {
      var ticker = pairIndexToTicker[pairIndex];
      if (ticker && typeof currentAssets !== 'undefined' && currentAssets[ticker] && currentAssets[ticker].current_price) {
        expectedPrice = BigInt(Math.round(currentAssets[ticker].current_price * 1e10));
      }
    }
    if (expectedPrice === BigInt(0)) {
      showToast('Could not fetch live price for this pair. Try again.', 'error');
      tradePending = false;
      return;
    }

    var closeAbi = ['function closeTradeMarket(uint32 _index, uint64 _expectedPrice)'];
    var diamond = new ethers.Contract(gtradeConfig.trading_contract, closeAbi, walletState.signer);
    showToast('Closing position...', 'info', 15000);
    var tx = await diamond.closeTradeMarket(tradeIndex, expectedPrice, { gasLimit: 3000000 });
    showToast('Close submitted. Waiting for confirmation...', 'info', 20000);
    var receipt = await tx.wait();
    showToast(
      'Position closed! <a href="https://arbiscan.io/tx/' + receipt.hash + '" target="_blank" rel="noopener">View on Arbiscan</a>',
      'success', 10000
    );
    // Record closed trade to localStorage history
    var cached = _openTradesCache[tradeIndex] || {};
    var closePriceFloat = Number(expectedPrice) / 1e10;
    var entryP = cached.openPrice ? parseFloat(cached.openPrice) / 1e10 : 0;
    var levNum = cached.leverage ? parseFloat(cached.leverage) / 1000 : 0;
    var colIdx = parseInt(cached.collateralIndex || '3');
    var colDec = (colIdx === 3) ? 6 : 18;
    var colNum = cached.collateralAmount ? Number(BigInt(cached.collateralAmount)) / Math.pow(10, colDec) : 0;
    var pnlPct = 0;
    if (entryP > 0 && levNum > 0) {
      pnlPct = cached.long
        ? ((closePriceFloat - entryP) / entryP) * levNum * 100
        : ((entryP - closePriceFloat) / entryP) * levNum * 100;
    }
    saveTradeToHistory({
      pairLabel: cached.pairLabel || ('Pair #' + pairIndex),
      dir: cached.dir || '?',
      lev: cached.lev || '?x',
      long: !!cached.long,
      collateral: colNum.toFixed(2),
      entryPrice: entryP.toFixed(2),
      closePrice: closePriceFloat.toFixed(2),
      pnlUsd: (colNum * (pnlPct / 100)).toFixed(2),
      pnlPct: pnlPct.toFixed(1),
      txHash: receipt.hash,
      closedAt: new Date().toISOString()
    });
    await refreshUSDCBalance();
    // Poll for backend to index the closed trade
    pollOpenTrades(5, 3000);
  } catch (e) {
    var msg = e.reason || e.shortMessage || e.message || 'Close trade failed';
    if (e.code === 4001 || (e.info && e.info.error && e.info.error.code === 4001)) {
      msg = 'Transaction rejected by user';
    }
    if (msg.length > 150) msg = msg.slice(0, 150) + '...';
    showToast(msg, 'error', 8000);
  } finally {
    tradePending = false;
  }
}

// Listen for wallet account/chain changes
if (typeof window !== 'undefined' && window.ethereum) {
  window.ethereum.on('accountsChanged', function(accounts) {
    if (accounts.length === 0) {
      walletState = { connected: false, address: null, provider: null, signer: null, chainId: null, usdcBalance: '0' };
      updateWalletUI();
      validateTradeClient();
    } else {
      walletState.address = accounts[0];
      updateWalletUI();
      refreshUSDCBalance();
      loadOpenTrades();
      loadTradeHistory();
      validateTradeClient();
    }
  });
  window.ethereum.on('chainChanged', function(chainId) {
    walletState.chainId = parseInt(chainId, 16);
    updateWalletUI();
    refreshUSDCBalance();
    loadOpenTrades();
    loadTradeHistory();
    validateTradeClient();
  });
}

// Initialize gTrade config on load
(async function initGtradeConfig() {
  try {
    var resp = await fetch('/api/gtrade/config');
    gtradeConfig = await resp.json();
    populateTradeAssets();
    validateTradeClient();
  } catch (e) {
    console.warn('Could not load gTrade config:', e);
  }
})();

/* Silent auto-reconnect on page load */
if (typeof window !== 'undefined' && window.ethereum) {
  window.ethereum.request({ method: 'eth_accounts' }).then(function(accounts) {
    if (accounts.length > 0) { connectWallet(); }
  }).catch(function() {});
}
