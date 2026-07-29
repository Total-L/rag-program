# L20 — PII 脱敏 样例

## 原文
```

# 项目交接备忘

联系人：13800138000（王工程师）
身份证号：110101199003078888
邮箱：alice@corp.example.com
工单系统登录：http://10.0.5.123:8080

数据库连接：postgres://admin:Sup3rS3cret@db.corp.example.com:5432/prod
临时 API key：REDACTED_OPENAI_KEY_PLACEHOLDER

JWT：eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.signature_xyz

薪资：年薪 50 万（含期权）

```

## 脱敏后（写库）
```

# 项目交接备忘

联系人：*******8000（王工程师）
身份证号：**************8888
邮箱：[EMAIL]
工单系统登录：http://[EMAIL]:8080

数据库连接：postgres://admin:[EMAIL]:5432/prod
临时 API key：REDACTED_OPENAI_KEY_PLACEHOLDER

JWT：[REDACTED]

薪资：年薪 50 万（含期权）

```

## 自动分级
base=public → **restricted**  原因=['CN_MOBILE', 'CN_ID_CARD', 'BANK_CARD', 'EMAIL', 'IPV4', 'JWT', 'DB_DSN']

## 三道防线
1. **写库前**：密钥 DROP、个人信息 MASK（保留尾号）
2. **写 trace 前**：一律 HASH + 截断
3. **自动升级 sensitivity**：classify_sensitivity 只升不降

## 顺序
classify_sensitivity 必须在 redact_for_index **之前** ——
一旦脱敏证件号被遮蔽，分类器就检不出来了。

## 边界
正则覆盖结构化 PII，**覆盖不了**自由文本里的姓名/地址/病情。
真上生产要接 Presidio / NER。本模块不假装解决了全部问题。
