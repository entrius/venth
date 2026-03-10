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

/* ========== Liquidation Price (client-side) ========== */
function calculateLiquidationPrice(entryPrice, isLong, leverage) {
  if (leverage <= 0 || entryPrice <= 0) return 0;
  var threshold = 0.9; // 90% loss triggers liquidation
  if (isLong) return entryPrice * (1 - threshold / leverage);
  return entryPrice * (1 + threshold / leverage);
}

/* ========== Trade Management Panel ========== */
function toggleManagePanel(tradeIndex) {
  var panel = document.getElementById('manage-panel-' + tradeIndex);
  if (!panel) return;
  panel.classList.toggle('open');
}

async function updateTradeTP(tradeIndex, remove) {
  if (!walletState.connected || !walletState.signer || tradePending) return;
  if (walletState.chainId !== 42161) { showToast('Switch to Arbitrum', 'error'); return; }
  var cached = _openTradesCache[tradeIndex];
  // Warn if stock pair and market is closed (oracle callbacks may fail)
  if (cached) {
    var ticker = pairIndexToTicker[cached.pairIdx] || pairIndexToTicker[String(cached.pairIdx)];
    if (ticker && gtradeConfig && gtradeConfig.pairs && gtradeConfig.pairs[ticker] && gtradeConfig.pairs[ticker].group === 'stocks') {
      var mkt = await checkMarketStatus();
      if (!mkt.open) { showToast(mkt.reason + '. TP/SL updates on stocks may fail.', 'error', 6000); }
    }
  }
  var newTp;
  if (remove) {
    newTp = BigInt(0);
  } else {
    var input = document.getElementById('manage-tp-' + tradeIndex);
    var tpPrice = parseFloat(input ? input.value : '');
    if (isNaN(tpPrice) || tpPrice <= 0) { showToast('Enter a valid TP price', 'error'); return; }
    // Skip if value hasn't changed
    if (cached) {
      var currentTp = cached.tp ? parseFloat(cached.tp) / 1e10 : 0;
      if (Math.abs(tpPrice - currentTp) < 0.005) { showToast('TP is already set to $' + currentTp.toFixed(2), 'info'); return; }
    }
    // Validate TP direction and max distance
    if (cached) {
      var entryPrice = cached.openPrice ? parseFloat(cached.openPrice) / 1e10 : 0;
      if (entryPrice > 0) {
        if (cached.long && tpPrice <= entryPrice) { showToast('TP must be above entry price ($' + entryPrice.toFixed(2) + ') for longs', 'error'); return; }
        if (!cached.long && tpPrice >= entryPrice) { showToast('TP must be below entry price ($' + entryPrice.toFixed(2) + ') for shorts', 'error'); return; }
        var tpPct = Math.abs(tpPrice - entryPrice) / entryPrice * 100;
        if (tpPct > 900) { showToast('TP cannot exceed 900% from entry price', 'error'); return; }
      }
    }
    newTp = BigInt(Math.round(tpPrice * 1e10));
  }
  tradePending = true;
  try {
    var abi = ['function updateTp(uint32 _index, uint64 _newTp)'];
    var diamond = new ethers.Contract(gtradeConfig.trading_contract, abi, walletState.signer);
    showToast(remove ? 'Removing TP...' : 'Updating TP...', 'info', 15000);
    var tx = await diamond.updateTp(tradeIndex, newTp, { gasLimit: 1500000 });
    await tx.wait();
    showToast(remove ? 'TP removed' : 'TP updated to $' + (Number(newTp) / 1e10).toFixed(2), 'success');
    // Optimistic UI + cache update: patch displayed TP and cache immediately (backend API has indexing delay)
    var tpDisplay = Number(newTp) / 1e10;
    var tpSpan = document.querySelector('[data-tp-trade="' + tradeIndex + '"]');
    if (tpSpan) tpSpan.textContent = remove ? '' : 'TP: $' + tpDisplay.toFixed(2);
    var tpInput = document.getElementById('manage-tp-' + tradeIndex);
    if (tpInput) tpInput.value = remove ? '' : tpDisplay.toFixed(2);
    if (_openTradesCache[tradeIndex]) _openTradesCache[tradeIndex].tp = String(newTp);
    pollOpenTrades(5, 6000);
  } catch (e) {
    var msg = e.reason || e.shortMessage || e.message || 'Update TP failed';
    if (e.code === 4001 || (e.info && e.info.error && e.info.error.code === 4001)) msg = 'Transaction rejected';
    showToast(msg.length > 120 ? msg.slice(0, 120) + '...' : msg, 'error');
  } finally { tradePending = false; }
}

async function updateTradeSL(tradeIndex, remove) {
  if (!walletState.connected || !walletState.signer || tradePending) return;
  if (walletState.chainId !== 42161) { showToast('Switch to Arbitrum', 'error'); return; }
  var cached = _openTradesCache[tradeIndex];
  // Warn if stock pair and market is closed
  if (cached) {
    var ticker = pairIndexToTicker[cached.pairIdx] || pairIndexToTicker[String(cached.pairIdx)];
    if (ticker && gtradeConfig && gtradeConfig.pairs && gtradeConfig.pairs[ticker] && gtradeConfig.pairs[ticker].group === 'stocks') {
      var mkt = await checkMarketStatus();
      if (!mkt.open) { showToast(mkt.reason + '. TP/SL updates on stocks may fail.', 'error', 6000); }
    }
  }
  var newSl;
  if (remove) {
    newSl = BigInt(0);
  } else {
    var input = document.getElementById('manage-sl-' + tradeIndex);
    var slPrice = parseFloat(input ? input.value : '');
    if (isNaN(slPrice) || slPrice <= 0) { showToast('Enter a valid SL price', 'error'); return; }
    // Skip if value hasn't changed
    if (cached) {
      var currentSl = cached.sl ? parseFloat(cached.sl) / 1e10 : 0;
      if (Math.abs(slPrice - currentSl) < 0.005) { showToast('SL is already set to $' + currentSl.toFixed(2), 'info'); return; }
    }
    // Validate SL direction and max distance (MAX_SL_P = 75, so max SL % = 75 / leverage)
    if (cached) {
      var entryPrice = cached.openPrice ? parseFloat(cached.openPrice) / 1e10 : 0;
      var levNum = cached.leverage ? parseFloat(cached.leverage) / 1000 : 0;
      if (entryPrice > 0) {
        if (cached.long && slPrice >= entryPrice) { showToast('SL must be below entry price ($' + entryPrice.toFixed(2) + ') for longs', 'error'); return; }
        if (!cached.long && slPrice <= entryPrice) { showToast('SL must be above entry price ($' + entryPrice.toFixed(2) + ') for shorts', 'error'); return; }
        if (levNum > 0) {
          var maxSlPct = 75 / levNum;
          var slPct = Math.abs(slPrice - entryPrice) / entryPrice * 100;
          if (slPct > maxSlPct) { showToast('SL too far from entry. Max distance at ' + levNum.toFixed(0) + 'x leverage: ' + maxSlPct.toFixed(2) + '%', 'error', 6000); return; }
        }
      }
    }
    newSl = BigInt(Math.round(slPrice * 1e10));
  }
  tradePending = true;
  try {
    var abi = ['function updateSl(uint32 _index, uint64 _newSl)'];
    var diamond = new ethers.Contract(gtradeConfig.trading_contract, abi, walletState.signer);
    showToast(remove ? 'Removing SL...' : 'Updating SL...', 'info', 15000);
    var tx = await diamond.updateSl(tradeIndex, newSl, { gasLimit: 1500000 });
    await tx.wait();
    showToast(remove ? 'SL removed' : 'SL updated to $' + (Number(newSl) / 1e10).toFixed(2), 'success');
    // Optimistic UI + cache update: patch displayed SL and cache immediately (backend API has indexing delay)
    var slDisplay = Number(newSl) / 1e10;
    var slSpan = document.querySelector('[data-sl-trade="' + tradeIndex + '"]');
    if (slSpan) slSpan.textContent = remove ? '' : 'SL: $' + slDisplay.toFixed(2);
    var slInput = document.getElementById('manage-sl-' + tradeIndex);
    if (slInput) slInput.value = remove ? '' : slDisplay.toFixed(2);
    if (_openTradesCache[tradeIndex]) _openTradesCache[tradeIndex].sl = String(newSl);
    pollOpenTrades(5, 6000);
  } catch (e) {
    var msg = e.reason || e.shortMessage || e.message || 'Update SL failed';
    if (e.code === 4001 || (e.info && e.info.error && e.info.error.code === 4001)) msg = 'Transaction rejected';
    showToast(msg.length > 120 ? msg.slice(0, 120) + '...' : msg, 'error');
  } finally { tradePending = false; }
}

async function decreasePosition(tradeIndex) {
  if (!walletState.connected || !walletState.signer || tradePending) return;
  if (walletState.chainId !== 42161) { showToast('Switch to Arbitrum', 'error'); return; }
  var input = document.getElementById('manage-decrease-' + tradeIndex);
  var amount = parseFloat(input ? input.value : '');
  if (isNaN(amount) || amount <= 0) { showToast('Enter a valid USDC amount', 'error'); return; }

  // Validate remaining position stays above protocol minimum ($1,500)
  var cached = _openTradesCache[tradeIndex];
  // Warn if stock pair and market is closed (oracle callback required for partial close)
  if (cached) {
    var _ticker = pairIndexToTicker[cached.pairIdx] || pairIndexToTicker[String(cached.pairIdx)];
    if (_ticker && gtradeConfig && gtradeConfig.pairs && gtradeConfig.pairs[_ticker] && gtradeConfig.pairs[_ticker].group === 'stocks') {
      var mkt = await checkMarketStatus();
      if (!mkt.open) { showToast(mkt.reason + '. Partial closes on stocks may fail.', 'error', 6000); return; }
    }
  }
  if (cached) {
    var colIdx = parseInt(cached.collateralIndex || '3');
    var colDecimals = (colIdx === 3) ? 6 : 18;
    var currentCol = Number(BigInt(cached.collateralAmount || '0')) / Math.pow(10, colDecimals);
    var levNum = cached.leverage ? parseFloat(cached.leverage) / 1000 : 0;
    var remainingCol = currentCol - amount;
    if (remainingCol < 0) { showToast('Amount exceeds position collateral (' + currentCol.toFixed(2) + ' USDC)', 'error'); return; }
    var remainingPosition = remainingCol * levNum;
    var minPosition = (gtradeConfig && gtradeConfig.group_limits) ? 1500 : 1500;
    if (remainingPosition < minPosition && remainingCol > 0) {
      showToast('Remaining position $' + remainingPosition.toFixed(0) + ' would be below $' + minPosition + ' minimum. Max decrease: ' +
        Math.max(0, currentCol - (minPosition / levNum)).toFixed(2) + ' USDC', 'error', 8000);
      return;
    }
  }

  var collateralDelta = ethers.parseUnits(amount.toString(), gtradeConfig.usdc_decimals);

  // Fetch current price for _expectedPrice (required by gTrade v9)
  var expectedPrice = BigInt(0);
  var ticker = null;
  if (cached) {
    ticker = pairIndexToTicker[cached.pairIdx] || pairIndexToTicker[String(cached.pairIdx)];
    var feedAddr = ticker ? CHAINLINK_FEEDS[ticker] : null;
    if (feedAddr && walletState.provider) {
      var livePrice = await fetchChainlinkPrice(feedAddr, walletState.provider);
      if (livePrice) expectedPrice = BigInt(Math.round(livePrice * 1e10));
    }
  }
  // Fallback: use Synth API cached price
  if (expectedPrice === BigInt(0) && ticker && typeof currentAssets !== 'undefined' &&
      currentAssets[ticker] && currentAssets[ticker].current_price) {
    expectedPrice = BigInt(Math.round(currentAssets[ticker].current_price * 1e10));
  }
  if (expectedPrice === BigInt(0)) {
    showToast('Could not fetch current price for partial close', 'error');
    return;
  }

  tradePending = true;
  try {
    var abi = ['function decreasePositionSize(uint32 _index, uint120 _collateralDelta, uint24 _leverageDelta, uint64 _expectedPrice)'];
    var diamond = new ethers.Contract(gtradeConfig.trading_contract, abi, walletState.signer);
    showToast('Decreasing position by ' + amount.toFixed(2) + ' USDC...', 'info', 15000);
    var tx = await diamond.decreasePositionSize(tradeIndex, collateralDelta, 0, expectedPrice, { gasLimit: 3000000 });
    showToast('Partial close submitted...', 'info', 20000);
    await tx.wait();
    showToast('Position decreased by ' + amount.toFixed(2) + ' USDC', 'success');
    await refreshUSDCBalance();
    // Optimistic UI + cache update for collateral
    if (cached) {
      var colIdx = parseInt(cached.collateralIndex || '3');
      var colDecimals = (colIdx === 3) ? 6 : 18;
      var oldCol = Number(BigInt(cached.collateralAmount || '0')) / Math.pow(10, colDecimals);
      var newCol = oldCol - amount;
      var colSpan = document.querySelector('[data-col-trade="' + tradeIndex + '"]');
      if (colSpan) colSpan.textContent = newCol.toFixed(2) + ' USDC';
      var newColRaw = BigInt(cached.collateralAmount || '0') - BigInt(collateralDelta);
      _openTradesCache[tradeIndex].collateralAmount = String(newColRaw);
    }
    pollOpenTrades(5, 6000);
  } catch (e) {
    var msg = e.reason || e.shortMessage || e.message || 'Decrease position failed';
    if (e.code === 4001 || (e.info && e.info.error && e.info.error.code === 4001)) msg = 'Transaction rejected';
    showToast(msg.length > 120 ? msg.slice(0, 120) + '...' : msg, 'error');
  } finally { tradePending = false; }
}

/* ========== Market Hours Check ========== */
var _marketStatusCache = { open: null, reason: '', ts: 0 };

async function checkMarketStatus() {
  var now = Date.now();
  if (_marketStatusCache.open !== null && (now - _marketStatusCache.ts) < 60000) {
    return _marketStatusCache;
  }
  try {
    var resp = await fetch('/api/gtrade/market-status');
    var data = await resp.json();
    _marketStatusCache = { open: data.open, reason: data.reason, ts: now };
    return _marketStatusCache;
  } catch (_) {
    return { open: true, reason: '' };
  }
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
  if (tp > 0) {
    var tpTarget = direction === 'long' ? price * (1 + tp / 100) : price * (1 - tp / 100);
    html += '<div class="preview-row"><span>Take Profit</span><span class="positive">$' + fmt(tpTarget) + ' (+' + tp + '%)</span></div>';
  }
  if (sl > 0) {
    var slTarget = direction === 'long' ? price * (1 - sl / 100) : price * (1 + sl / 100);
    html += '<div class="preview-row"><span>Stop Loss</span><span class="negative">$' + fmt(slTarget) + ' (-' + sl + '%)</span></div>';
  }
  html += '<div class="preview-row"><span>Max Slippage</span><span>' + slippage.toFixed(1) + '%</span></div>';

  // Liquidation price estimate
  if (price > 0 && leverage > 0) {
    var liqLong = calculateLiquidationPrice(price, direction === 'long', leverage);
    html += '<div class="preview-row"><span>Est. Liq. Price</span><span class="negative">$' + fmt(liqLong) + '</span></div>';
  }

  html += '<div class="preview-row"><span>Protocol</span><span>gTrade &middot; Arbitrum</span></div>';
  preview.innerHTML = html;

  // Fee estimation (async)
  if (asset && collateral > 0 && leverage > 0) {
    fetch('/api/gtrade/estimate-fees', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ asset: asset, collateral_usd: collateral, leverage: leverage })
    }).then(function(r) { return r.json(); }).then(function(fees) {
      if (fees.error) return;
      var existing = preview.querySelector('.fee-estimate');
      if (existing) existing.remove();
      var feeHtml = '<div class="fee-estimate">' +
        '<div class="fee-row"><span>Open Fee (' + fees.fee_pct + '%)</span><span>$' + fees.open_fee.toFixed(2) + '</span></div>' +
        '<div class="fee-row"><span>Close Fee (est.)</span><span>$' + fees.close_fee.toFixed(2) + '</span></div>' +
        '<div class="fee-row fee-total"><span>Total Fees</span><span>$' + fees.total_fee.toFixed(2) + '</span></div>' +
        '</div>';
      preview.innerHTML += feeHtml;
    }).catch(function() {});
  }

  // Market hours warning for stocks (async)
  if (gtradeConfig && gtradeConfig.pairs && gtradeConfig.pairs[asset]) {
    var pairGroup = gtradeConfig.pairs[asset].group;
    if (pairGroup === 'stocks') {
      checkMarketStatus().then(function(status) {
        var existing = preview.querySelector('.market-warning');
        if (existing) existing.remove();
        if (!status.open) {
          preview.innerHTML += '<div class="market-warning">\u26A0 ' + status.reason +
            '. Stock trades will revert on-chain.</div>';
        }
      });
    }
  }
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

  // Block stock trades when market is closed
  if (gtradeConfig && gtradeConfig.pairs && gtradeConfig.pairs[asset] && gtradeConfig.pairs[asset].group === 'stocks') {
    var mktStatus = await checkMarketStatus();
    if (!mktStatus.open) {
      showToast(mktStatus.reason + '. Stock trades will revert.', 'error', 8000);
      return;
    }
  }

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
  // Long: TP above entry, SL below. Short: TP below entry, SL above.
  var tpScaled = 0;
  var slScaled = 0;
  if (direction === 'long') {
    if (tpPct > 0) tpScaled = BigInt(Math.round(currentPrice * (1 + tpPct / 100) * 1e10));
    if (slPct > 0) slScaled = BigInt(Math.round(currentPrice * (1 - slPct / 100) * 1e10));
  } else {
    if (tpPct > 0) tpScaled = BigInt(Math.round(currentPrice * (1 - tpPct / 100) * 1e10));
    if (slPct > 0) slScaled = BigInt(Math.round(currentPrice * (1 + slPct / 100) * 1e10));
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
        leverage: t.leverage, collateralAmount: t.collateralAmount, collateralIndex: t.collateralIndex, openPrice: t.openPrice,
        tp: t.tp || '0', sl: t.sl || '0' };
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
        pnlHtml = '<span class="trade-pnl ' + pnlClass + '" data-pnl-trade="' + tradeIdx + '">' +
          'Est. ' + pnlSign + pnlUsd.toFixed(2) + ' USDC (' + pnlSign + pnlPct.toFixed(2) + '%)' +
          '</span>';
      }

      // Liquidation price
      var liqPrice = calculateLiquidationPrice(entryPrice, !!t.long, levNum);
      var liqHtml = liqPrice > 0 ? '<span class="liq-price">Liq: $' + liqPrice.toFixed(2) + '</span>' : '';

      // Current TP/SL from trade data
      var tpRaw = t.tp ? parseFloat(t.tp) / 1e10 : 0;
      var slRaw = t.sl ? parseFloat(t.sl) / 1e10 : 0;
      var tpSlHtml = '';
      if (tpRaw > 0) tpSlHtml += '<span data-tp-trade="' + tradeIdx + '" style="color:var(--positive);font-size:10px">TP: $' + tpRaw.toFixed(2) + '</span> ';
      if (slRaw > 0) tpSlHtml += '<span data-sl-trade="' + tradeIdx + '" style="color:var(--negative);font-size:10px">SL: $' + slRaw.toFixed(2) + '</span>';

      // Manage panel HTML
      var manageHtml =
        '<div class="manage-panel" id="manage-panel-' + tradeIdx + '">' +
        '<div class="manage-row">' +
        '<label>Take Profit</label>' +
        '<input type="number" id="manage-tp-' + tradeIdx + '" placeholder="Price ($)" step="0.01"' +
        (tpRaw > 0 ? ' value="' + tpRaw.toFixed(2) + '"' : '') + '>' +
        '<button class="manage-action-btn update" onclick="updateTradeTP(' + tradeIdx + ')">Set TP</button>' +
        (tpRaw > 0 ? '<button class="manage-action-btn remove" onclick="updateTradeTP(' + tradeIdx + ',true)">Remove</button>' : '') +
        '</div>' +
        '<div class="manage-row">' +
        '<label>Stop Loss</label>' +
        '<input type="number" id="manage-sl-' + tradeIdx + '" placeholder="Price ($)" step="0.01"' +
        (slRaw > 0 ? ' value="' + slRaw.toFixed(2) + '"' : '') + '>' +
        '<button class="manage-action-btn update" onclick="updateTradeSL(' + tradeIdx + ')">Set SL</button>' +
        (slRaw > 0 ? '<button class="manage-action-btn remove" onclick="updateTradeSL(' + tradeIdx + ',true)">Remove</button>' : '') +
        '</div>' +
        '<div class="manage-row">' +
        '<label>Partial Close</label>' +
        '<input type="number" id="manage-decrease-' + tradeIdx + '" placeholder="USDC" step="0.01">' +
        '<button class="manage-action-btn decrease" onclick="decreasePosition(' + tradeIdx + ')">Decrease</button>' +
        '</div>' +
        '</div>';

      html += '<div class="open-trade-row" style="flex-wrap:wrap">' +
        '<div class="trade-row-info">' +
        '<div class="trade-row-main">' +
        '<span class="' + dirClass + '">' + dir + ' ' + lev + '</span>' +
        '<span>' + pairLabel + '</span>' +
        '<span>Entry: ' + entryFmt + (curPrice ? ' / Now: $' + curPrice.toFixed(2) : '') + '</span>' +
        '<span data-col-trade="' + tradeIdx + '">' + colFmt + ' USDC</span>' +
        liqHtml +
        '</div>' +
        (tpSlHtml ? '<div style="margin-top:2px">' + tpSlHtml + '</div>' : '') +
        (pnlHtml ? '<div class="trade-row-pnl">' + pnlHtml + '</div>' : '') +
        '</div>' +
        '<div style="display:flex;gap:4px;align-items:center;flex-shrink:0">' +
        '<button class="manage-btn" onclick="toggleManagePanel(' + tradeIdx + ')" title="Manage TP/SL & partial close">Manage</button>' +
        '<button class="close-trade-btn" onclick="closeTrade(' + tradeIdx + ',' + pairIdx + ')" title="Close position">&#x2715;</button>' +
        '</div>' +
        manageHtml +
        '</div>';
    });
    // Skip full re-render if a manage panel is open (prevents input flicker)
    var hasOpenPanel = container.querySelector('.manage-panel.open');
    if (hasOpenPanel) {
      // Still update P&L and price text in-place without replacing HTML
      trades.forEach(function(item) {
        var t = item.trade || item;
        var tradeIdx = parseInt(t.index || '0');
        var pairIdx = parseInt(t.pairIndex || '0');
        var curPrice = livePrices[pairIdx];
        var pnlEl = container.querySelector('[data-pnl-trade="' + tradeIdx + '"]');
        if (pnlEl && curPrice) {
          var entryP = t.openPrice ? parseFloat(t.openPrice) / 1e10 : 0;
          var levNum = t.leverage ? parseFloat(t.leverage) / 1000 : 0;
          var colRaw = BigInt(t.collateralAmount || '0');
          var colIdx2 = parseInt(t.collateralIndex || '3');
          var colDec = (colIdx2 === 3) ? 6 : 18;
          var col2 = Number(colRaw) / Math.pow(10, colDec);
          if (entryP > 0 && levNum > 0) {
            var pct = t.long ? ((curPrice - entryP) / entryP) * levNum * 100 : ((entryP - curPrice) / entryP) * levNum * 100;
            var usd = col2 * (pct / 100);
            var cls = usd >= 0 ? 'positive' : 'negative';
            var sgn = usd >= 0 ? '+' : '';
            pnlEl.className = 'trade-pnl ' + cls;
            pnlEl.textContent = 'Est. ' + sgn + usd.toFixed(2) + ' USDC (' + sgn + pct.toFixed(2) + '%)';
          }
        }
      });
      return;
    }
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<div class="no-trades">Could not load trades</div>';
  }
}

async function loadTradeHistory() {
  var container = document.getElementById('trade-history-list');
  if (!container) return;
  if (!walletState.connected) return;

  // Fetch from gTrade backend (includes liquidations, TP/SL hits, all protocol-closed trades)
  var backendTrades = [];
  var pairNames = {};
  try {
    var resp = await fetch('/api/gtrade/trade-history?address=' + walletState.address);
    var data = await resp.json();
    backendTrades = data.history || [];
    pairNames = data.pair_names || {};
  } catch (_) {}

  // Build merged history: backend trades + localStorage-only entries
  var localHistory = getTradeHistory();
  var localByTx = {};
  localHistory.forEach(function(h) { if (h.txHash) localByTx[h.txHash.toLowerCase()] = h; });

  var merged = [];

  // Process backend trades (authoritative source for all closed trades including liquidations)
  backendTrades.forEach(function(item) {
    var t = item.trade || item;
    if (t.isOpen) return; // skip still-open trades
    var pairIdx = parseInt(t.pairIndex || '0');
    var dir = t.long ? 'LONG' : 'SHORT';
    var lev = t.leverage ? (parseFloat(t.leverage) / 1000).toFixed(0) + 'x' : '?x';
    var levNum = t.leverage ? parseFloat(t.leverage) / 1000 : 0;
    var colRaw = BigInt(t.collateralAmount || '0');
    var colIdx = parseInt(t.collateralIndex || '3');
    var colDecimals = (colIdx === 3) ? 6 : 18;
    var col = Number(colRaw) / Math.pow(10, colDecimals);
    var entryPrice = t.openPrice ? (parseFloat(t.openPrice) / 1e10) : 0;
    var pairLabel = pairNames[pairIdx] || ('Pair #' + pairIdx);

    // Extract close data
    var closeData = item.closeTradeData || item.close_trade_data || {};
    var closePrice = closeData.closePrice ? parseFloat(closeData.closePrice) / 1e10 : 0;
    var pnlUsd = null;
    var pnlPct = null;

    if (closePrice > 0 && entryPrice > 0 && levNum > 0) {
      var pctRaw = t.long
        ? ((closePrice - entryPrice) / entryPrice) * levNum * 100
        : ((entryPrice - closePrice) / entryPrice) * levNum * 100;
      pnlUsd = col * (pctRaw / 100);
      pnlPct = pctRaw;
    }

    // Detect liquidation: close type or -90%+ loss
    var isLiquidation = false;
    if (closeData.closeType !== undefined) {
      // gTrade closeType: 0=market, 1=TP, 2=SL, 3=LIQ
      isLiquidation = parseInt(closeData.closeType) === 3;
    } else if (pnlPct !== null && pnlPct <= -89) {
      isLiquidation = true;
    }

    merged.push({
      dir: dir, lev: lev, long: !!t.long,
      pairLabel: pairLabel,
      collateral: col.toFixed(2),
      entryPrice: entryPrice.toFixed(2),
      closePrice: closePrice > 0 ? closePrice.toFixed(2) : '?',
      pnlUsd: pnlUsd !== null ? pnlUsd.toFixed(2) : 'pending',
      pnlPct: pnlPct !== null ? pnlPct.toFixed(1) : 'pending',
      txHash: null,
      closedAt: null,
      isLiquidation: isLiquidation,
      _source: 'backend'
    });
  });

  // Add localStorage-only entries (recent closes that backend hasn't indexed yet)
  localHistory.forEach(function(h) {
    // Check if this local entry is already covered by a backend entry
    // (match by entry price + collateral + direction as a rough dedup)
    var dominated = merged.some(function(m) {
      return m.entryPrice === h.entryPrice && m.collateral === h.collateral && m.dir === h.dir;
    });
    if (!dominated) {
      h._source = 'local';
      merged.push(h);
    } else if (h.txHash) {
      // Enrich the backend entry with the local tx hash
      for (var i = 0; i < merged.length; i++) {
        if (merged[i].entryPrice === h.entryPrice && merged[i].collateral === h.collateral && merged[i].dir === h.dir) {
          if (!merged[i].txHash) merged[i].txHash = h.txHash;
          if (!merged[i].closedAt && h.closedAt) merged[i].closedAt = h.closedAt;
          break;
        }
      }
    }
  });

  // Limit to 20 most recent
  merged = merged.slice(0, 20);

  if (merged.length === 0) {
    container.innerHTML = '<div class="no-trades">No trade history</div>';
    return;
  }

  var html = '';
  merged.forEach(function(h) {
    var dirClass = h.long ? 'positive' : 'negative';
    var pnlHtml = '';
    if (h.pnlUsd === 'pending' || h.pnlPct === 'pending') {
      pnlHtml = '<span class="trade-pnl" style="color:var(--text-muted)">P&L pending...</span>';
    } else {
      var pnlVal = parseFloat(h.pnlUsd || '0');
      var pnlPctVal = parseFloat(h.pnlPct || '0');
      var pnlClass = pnlVal >= 0 ? 'positive' : 'negative';
      var pnlSign = pnlVal >= 0 ? '+' : '';
      pnlHtml = '<span class="trade-pnl ' + pnlClass + '">' +
        pnlSign + pnlVal.toFixed(2) + ' USDC (' + pnlSign + pnlPctVal.toFixed(1) + '%)</span>';
    }
    var txLink = h.txHash
      ? ' <a href="https://arbiscan.io/tx/' + h.txHash + '" target="_blank" rel="noopener" style="color:var(--accent);font-size:10px">tx</a>'
      : '';
    var badge = h.isLiquidation ? 'LIQUIDATED' : 'CLOSED';
    var badgeClass = h.isLiquidation ? 'history-badge liq-badge' : 'history-badge';
    // Format timestamp
    var timeStr = '';
    if (h.closedAt) {
      var d = new Date(h.closedAt);
      var ago = Math.floor((Date.now() - d.getTime()) / 1000);
      if (ago < 60) timeStr = ago + 's ago';
      else if (ago < 3600) timeStr = Math.floor(ago / 60) + 'm ago';
      else if (ago < 86400) timeStr = Math.floor(ago / 3600) + 'h ago';
      else timeStr = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    }
    html += '<div class="open-trade-row history-row">' +
      '<div class="trade-row-info">' +
      '<div class="trade-row-main">' +
      '<span class="' + dirClass + '">' + (h.dir || '?') + ' ' + (h.lev || '?x') + '</span>' +
      '<span>' + (h.pairLabel || '?') + '</span>' +
      '<span>Entry: $' + (h.entryPrice || '?') + ' / Close: $' + (h.closePrice || '?') + '</span>' +
      '<span>' + (h.collateral || '?') + ' USDC</span>' +
      (timeStr ? '<span style="color:var(--text-muted)">' + timeStr + '</span>' : '') +
      '</div>' +
      '<div class="trade-row-pnl">' + pnlHtml + txLink + '</div>' +
      '</div>' +
      '<span class="' + badgeClass + '">' + badge + '</span>' +
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

    // Snapshot USDC balance before close (oracle callback transfers USDC in a later tx)
    var erc20Abi = ['function balanceOf(address) view returns (uint256)'];
    var usdcContract = new ethers.Contract(gtradeConfig.usdc_contract, erc20Abi, walletState.provider);
    var balBefore = Number(ethers.formatUnits(
      await usdcContract.balanceOf(walletState.address), gtradeConfig.usdc_decimals));

    var closeAbi = ['function closeTradeMarket(uint32 _index, uint64 _expectedPrice)'];
    var diamond = new ethers.Contract(gtradeConfig.trading_contract, closeAbi, walletState.signer);
    showToast('Closing position...', 'info', 15000);
    var tx = await diamond.closeTradeMarket(tradeIndex, expectedPrice, { gasLimit: 3000000 });
    showToast('Close submitted. Waiting for confirmation...', 'info', 20000);
    var receipt = await tx.wait();

    // Immediately update UI: remove closed trade from open positions list
    var cached = _openTradesCache[tradeIndex] || {};
    delete _openTradesCache[tradeIndex];
    var openContainer = document.getElementById('open-trades-list');
    if (openContainer) {
      var btns = openContainer.querySelectorAll('.close-trade-btn');
      btns.forEach(function(btn) {
        if (btn.getAttribute('onclick') && btn.getAttribute('onclick').indexOf('closeTrade(' + tradeIndex + ',') !== -1) {
          var row = btn.closest('.open-trade-row');
          if (row) row.remove();
        }
      });
      if (!openContainer.querySelector('.open-trade-row')) {
        openContainer.innerHTML = '<div class="no-trades">No open positions</div>';
      }
    }

    showToast(
      'Position closed! Waiting for oracle settlement... <a href="https://arbiscan.io/tx/' + receipt.hash + '" target="_blank" rel="noopener">View on Arbiscan</a>',
      'success', 25000
    );

    // gTrade two-step: closeTradeMarket initiates close, oracle callback
    // settles it in a separate tx. Poll balance until USDC arrives.
    var closePriceFloat = Number(expectedPrice) / 1e10;
    var entryP = cached.openPrice ? parseFloat(cached.openPrice) / 1e10 : 0;
    var colIdx = parseInt(cached.collateralIndex || '3');
    var colDec = (colIdx === 3) ? 6 : 18;
    var colNum = cached.collateralAmount ? Number(BigInt(cached.collateralAmount)) / Math.pow(10, colDec) : 0;

    // Poll for oracle callback (up to 30s, every 2s)
    var balAfter = balBefore;
    for (var attempt = 0; attempt < 15; attempt++) {
      await new Promise(function(r) { setTimeout(r, 2000); });
      balAfter = Number(ethers.formatUnits(
        await usdcContract.balanceOf(walletState.address), gtradeConfig.usdc_decimals));
      if (Math.abs(balAfter - balBefore) > 0.001) break;
    }

    var usdcReturned = balAfter - balBefore;
    var actualPnlUsd = usdcReturned - colNum;
    var pnlPct = colNum > 0 ? (actualPnlUsd / colNum) * 100 : 0;
    // If balance didn't change (oracle slow or full loss), mark as pending
    var pnlResolved = Math.abs(balAfter - balBefore) > 0.001;
    saveTradeToHistory({
      pairLabel: cached.pairLabel || ('Pair #' + pairIndex),
      dir: cached.dir || '?',
      lev: cached.lev || '?x',
      long: !!cached.long,
      collateral: colNum.toFixed(2),
      entryPrice: entryP.toFixed(2),
      closePrice: closePriceFloat.toFixed(2),
      pnlUsd: pnlResolved ? actualPnlUsd.toFixed(2) : 'pending',
      pnlPct: pnlResolved ? pnlPct.toFixed(1) : 'pending',
      txHash: receipt.hash,
      closedAt: new Date().toISOString()
    });
    loadTradeHistory();
    await refreshUSDCBalance();
    if (pnlResolved) {
      showToast('Settlement complete: ' + (actualPnlUsd >= 0 ? '+' : '') + actualPnlUsd.toFixed(2) + ' USDC', actualPnlUsd >= 0 ? 'success' : 'error', 8000);
    }
    // Poll for backend to sync
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
