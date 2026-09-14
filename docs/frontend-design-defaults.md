# 前端／GUI 的預設設計

> 適用於 `implementer-frontend`，Web 與原生 GUI 都算。
> token 檔本身是 `design.tokens.json`，驗證由 `scripts/check-design-tokens.py` 做。

## 1. 為什麼需要這個

`implementer-frontend.md` 的規則寫著「只做 task-spec 要求的畫面與互動」。
但有一類決定**不做也得做**：顏色、圓角、間距、字級、過渡時間。task-spec 幾乎不會寫
「這個按鈕的內距是 12」，於是實作者一定會自己挑一個。

這就產生兩個問題，而且兩個都是**沉默**的：

- **跨 task 漂移**：每個 task 各挑一套，單獨看都合理，放在一起就不像同一個產品
- **檢驗者沒有依據**：「這個間距跟別的畫面不一樣」不在任何驗收標準裡，
  `verifier-reviewer` 想退也退不掉

所以設計基準要跟測試路徑、版本號來源一樣：**由專案宣告，實作者遵循，檢驗者可驗。**

## 2. 必須先問，不能默默套用

模板附了一份預設色票。這代表 clone 下來的專案會**默默繼承一套美學**——
而美學是使用者的決定，不是模板的。

**Orchestrator 在派工任何前端／GUI task 之前，要先問：**

> 「介面設計要套用模板的預設（現代、圓角、柔和色調、有過渡），
> 還是你有自己的設計規範／設計稿要遵循？」

三種答案：

| 回答 | 怎麼做 |
|---|---|
| **用預設** | `harness.config.json` 設 `"design": { "confirmed": true }` |
| **有自己的** | 依對方的規範改寫 `design.tokens.json`——**角色名字不變，只換值**；跑一次檢查器確認仍達 WCAG AA；再設 `confirmed` |
| **沒有圖形介面** | `"design": false` |

沒問過的話，`check-design-tokens.py` 與每個 session 的 `guard-selfcheck.py` 都會提醒。

**這個提醒刻意不做成 CI 紅燈。** 紅燈只會逼人隨手填一個 `true` 讓它閉嘴，
而不是真的去問使用者——那比沒有檢查更糟，因為之後所有人都會以為問過了。

這條規則屬於黃金法則第 7 條（需求不明確先發問），不是設計偏好。

## 3. 這份預設的方向

五句話，以及它們各自落在哪裡：

| 方向 | 落在哪 |
|---|---|
| **現代** | 層次靠**留白與字級**建立，不靠加框線；間距階梯上端給得寬；不擬物、不用陰影堆立體感 |
| **圓角** | `radius` 階梯（`sm` 6 / `md` 10 / `lg` 16 / `xl` 24），預設用 `md`；`none` 只留給真的要切齊的邊 |
| **柔和** | 避開純白與純黑（`surface` 是 `#FCFCFB`、`on-surface` 是 `#2B2F36`），主色與語意色都降飽和 |
| **直覺** | §6 的預設行為——可預測、可逆、狀態明確、用使用者的話 |
| **有過渡** | `motion` 的時間階梯與具名曲線；過渡是為了讓因果看得出來，不是裝飾 |

### 柔和與對比度是對立的——用檢查器當裁判

降飽和、避開純黑純白，很容易掉到 WCAG AA 以下。所以這份色票的值是
**用 `check-design-tokens.py` 逼近出來的「最柔和但仍合格」**，不是先挑好看再希望它過。

實際的邊界長這樣：主色候選 `#5B6FC9` 當文字畫在底色上是 **4.48**，門檻 4.5——
差 0.02 就出局；柔和綠 `#4A8466` 是 4.39。往回退一階的 `#5266BE`、`#42795C` 才過。

**這種差距用眼睛是看不出來的。** 這也是為什麼這件事必須是機制而不是品味。

## 4. 角色命名，不是色階命名

token 裡沒有 `gray-900`、`blue-500` 這種名字。只有**角色**：

```
surface / surface-muted        畫面底色、次要區塊底色
on-surface / on-surface-muted  畫在上面的主要文字、次要文字
border                         分隔線與外框
primary / on-primary           主要動作與它上面的文字
danger / success / warning     語意色，各自配一個 on-*
focus                          鍵盤焦點指示
```

**為什麼不用色階**：色階名字綁死了「它長什麼樣」。切到深色主題，`gray-900`
變成背景而不是文字——名字開始說謊。而角色名字在兩套主題下意思完全一樣，
所以**明暗兩套用同一組名字，切主題換的是值不是名字**。程式碼裡不該出現
「如果是深色主題就改用另一個顏色」這種分支。

這也是它跨得了技術棧的原因——每個 GUI 框架都有「背景/前景配對」這個概念。

## 5. 映射到各技術棧

token 是技術棧中立的資料。**實作者的工作是把它映射成目標框架的慣用寫法，
一次映射、全專案共用**，不是每個畫面各讀一次 JSON。

### Web（CSS custom properties）

```css
:root {
  --color-surface: #FCFCFB;
  --color-on-surface: #2B2F36;
  --space-3: 12px;
  --radius-md: 10px;
  --duration-normal: 200ms;
  --easing-standard: cubic-bezier(0.2, 0, 0, 1);
}
@media (prefers-color-scheme: dark) {
  :root { --color-surface: #1A1C20; --color-on-surface: #E4E4E1; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}
```

### Flutter

角色名字幾乎一對一——`ColorScheme` 本來就是這個設計：

```dart
ColorScheme.light(
  surface: Color(0xFFFCFCFB),
  onSurface: Color(0xFF2B2F36),
  primary: Color(0xFF5266BE),
  onPrimary: Color(0xFFFFFFFF),
  error: Color(0xFFB75048),      // danger
  onError: Color(0xFFFFFFFF),
)
```

`surface-muted` 對到 `surfaceContainerHighest`、`border` 對到 `outline`；
圓角放進 `ThemeData.cardTheme` / `filledButtonTheme` 的 `shape`；
過渡用 `AnimationController(duration:)` 搭 `Curves`，並看 `MediaQuery.disableAnimations`。

### SwiftUI / AppKit

放進 Asset Catalog 的具名顏色（每個名字設 Any/Dark 兩個外觀），程式碼只寫名字：

```swift
Text("標題").foregroundStyle(Color("on-surface"))
    .background(Color("surface"))
    .clipShape(RoundedRectangle(cornerRadius: 10))
```

減少動態效果看 `@Environment(\.accessibilityReduceMotion)`。

### Qt（Widgets）

對到 `QPalette` 的角色：`Window`/`WindowText` ≈ `surface`/`on-surface`、
`Base`/`Text` ≈ `surface-muted`/`on-surface`、`Highlight`/`HighlightedText` ≈
`primary`/`on-primary`、`Mid` ≈ `border`。圓角與過渡走 Qt Style Sheet 與
`QPropertyAnimation`。

### WPF / WinUI

`ResourceDictionary` 裡的具名 brush，用 `ThemeDictionaries` 分明暗：

```xml
<SolidColorBrush x:Key="SurfaceBrush" Color="#FCFCFB" />
<SolidColorBrush x:Key="OnSurfaceBrush" Color="#2B2F36" />
<CornerRadius x:Key="RadiusMd">10</CornerRadius>
```

### 其他

Android Compose 用 `MaterialTheme.colorScheme`（同 Flutter 的角色名）；
GTK 用 CSS 的 `@define-color`；終端機 TUI 就直接對到它的顏色常數。
**共通做法都是：建一層具名的主題物件，畫面只引用名字。**

## 6. 規格沒講時的預設行為

這些不是「加分項」，是 task-spec 沒寫也要做到的底線。要排除必須在 task-spec
**明確寫出來**（`implementer-frontend.md` 的規則第 5 條）。

### 直覺化：四條

| 原則 | 具體意思 |
|---|---|
| **可預測** | 同樣的東西長一樣、放同一個位置、叫同一個名字。同一個動作在兩個畫面不該有兩種樣子 |
| **可逆** | 破壞性動作要嘛可取消、要嘛可復原。「確定要刪除嗎？」不如一個「已刪除・復原」的提示條 |
| **狀態明確** | 系統在做什麼要說出來。按下去沒反應是最傷信任的一種失敗 |
| **用使用者的話** | 標籤與錯誤訊息用對方的詞彙，不是資料表欄位名或例外類別名 |

### 狀態與可及性

| 項目 | 預設 |
|---|---|
| **狀態齊全** | 每個會等待或可能失敗的畫面都要有：載入中／成功／錯誤／**空資料**。「空資料」最常被漏掉 |
| **錯誤要說得出下一步** | 不是「發生錯誤」，而是「儲存失敗，請檢查網路後重試」 |
| **鍵盤可達** | 所有互動元素可用鍵盤到達與觸發，焦點用 `focus` 角色畫得出來（不要 `outline: none` 了事） |
| **對比度** | 照 token 的配對走，見 §7 |
| **觸控目標** | 至少 44×44——這是手指的物理尺寸，不是設計偏好 |

### 圓角怎麼用

預設 `md`。輸入框與按鈕 `md`，卡片與對話框 `lg`，大面板 `xl`，頭像與標籤 `pill`。
`none` 只留給真的需要切齊的邊（表格儲存格、貼齊視窗的側欄）。
**同一個畫面不要出現三種以上的圓角**——那看起來不是風格，是沒對齊。

### 過渡怎麼用

| 情況 | 時間 | 曲線 |
|---|---|---|
| 懸停、按下、焦點 | `fast` | `standard` |
| 展開／收合、頁面內切換 | `normal` | `standard` |
| 進場（對話框、抽屜） | `normal` | `decelerate` |
| 離場 | `fast` | `accelerate` |

三條規則：

1. **動的是狀態改變，不是裝飾。** 過渡要讓人看出「這個東西從哪裡來、變成了什麼」。
   沒有因果關係的動畫是雜訊。
2. **不要動會擋住閱讀的東西。** 文字內容本身不做進場動畫。
3. **尊重「減少動態效果」的系統設定**（`prefers-reduced-motion`、
   `accessibilityReduceMotion`、`MediaQuery.disableAnimations`）。
   這不是可選項——對前庭功能敏感的人來說，那會造成實際的不適。
   `motion.reduced_motion` 必須是 `"respect"`，檢查器會擋下其他值。

### 不自己發明值

顏色、圓角、間距、字級、過渡時間一律從 token 取。需要一個 token 裡沒有的值，
代表**要嘛用錯角色、要嘛該改 token**（見 §8），回報 Orchestrator，不要就地硬幹一個。

## 7. 可機械驗的部分：對比度

設計好不好看驗不了，但**對比度算得出來**。`contrast_pairs` 宣告哪些配對必須可讀，
`check-design-tokens.py` 對明暗兩套各算一次，不到 WCAG AA 就 exit 1。

```bash
python3 scripts/check-design-tokens.py          # 結構 + 對比度
python3 scripts/check-design-tokens.py --show   # 列出每一組的實際比值
```

門檻就是 WCAG 2.1 AA 的規定：文字 4.5、大字與非文字元件 3.0。

**這不是形式主義。** 這份模板附的預設色票，第一版的深色邊框算出來是
**2.99**，門檻 **3.00**——用眼睛看那跟 3.5 完全一樣。人工檢查抓不到 0.01 的差距，
程式抓得到；而這正是「可及性視為隱性驗收標準」從自律變成機制的地方。

檢查器也會講出**餘裕最小**的那一組。卡在門檻邊緣不算安全——下次有人微調顏色就跌破了。

結構檢查還會擋下：圓角階梯沒有由小到大、過渡時間沒有排序或超過 500ms、
`reduced_motion` 不是 `respect`、明暗兩套角色不一致、出現未定義的角色名。

## 8. 什麼時候該改 token

**不是在做某個畫面的時候。** 需要一個 token 裡沒有的值，先問哪一個才對：

1. **是不是用錯角色了？** 想要「比 `on-surface` 淡一點的文字」→ 那是 `on-surface-muted`，
   不是新的顏色。
2. **真的缺一個角色嗎？** 那就**明暗兩套一起加**，加進 `contrast_pairs`，
   跑一次檢查器，並在 task-spec 之外另開一個 task——改設計基準會影響所有畫面，
   不該夾帶在某個功能 task 裡（黃金法則第 4 條）。
3. **只是這一個畫面的特例？** 那通常代表設計本身有問題，回報 Orchestrator。

角色一多就會退化成色階（`primary-2`、`primary-light`…），所以檢查器會擋下
**未定義的角色名**：新增要是刻意的動作，不是順手加的。

## 9. 沒有圖形介面的專案

CLI 與函式庫不該被逼著維護一份用不到的色票。在 `harness.config.json` 設：

```json
{ "design": false }
```

檢查器會略過並回 0。刻意要「明確關掉」而不是「檔案不存在就算了」——
後者分不出「這個專案沒有介面」與「有人把 token 檔刪了」。

## 10. 這份基準不保證什麼（誠實記載）

- **不保證好看。** 它保證的是「一致」與「讀得到」，那是可以客觀判定的部分。
- **不保證符合品牌。** 附的色票是**可替換的起點**，不是規定。換成自己的值之後
  重跑檢查器就好——檢查的是關係（對比度、結構），不是那些特定的顏色。
- **不涵蓋版面與資訊架構。** 間距階梯管得了「間隔用哪個值」，管不了「這個東西
  該不該放在這裡」。那仍然是 task-spec 的事。
- **「直覺」那四條驗不了。** 可預測、可逆、狀態明確、用使用者的話——
  這些只能靠 `verifier-reviewer` 對著畫面看，沒有腳本抓得到。寫下來是為了讓它
  至少變成**可以被指著說「這條沒做到」**的東西，而不是一句模糊的「不好用」。
