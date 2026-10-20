const fs = require('fs');
const path = require('path');

// 1. Patch parser.js
const parserPath = path.resolve(__dirname, '../node_modules/@react-native/codegen/lib/parsers/typescript/parser.js');
if (fs.existsSync(parserPath)) {
  let content = fs.readFileSync(parserPath, 'utf8');
  if (!content.includes('typeName.right && typeName.right.name')) {
    content = content.replace(
      /getTypeAnnotationName\(typeAnnotation\) \{[\s\S]*?return typeAnnotation === null[\s\S]*?: _typeAnnotation\$typeN\.name;[\s\S]*?\}/,
      `getTypeAnnotationName(typeAnnotation) {
    var _typeAnnotation$typeN;
    const typeName =
      typeAnnotation === null || typeAnnotation === void 0
        ? void 0
        : typeAnnotation.typeName;
    if (!typeName) return void 0;
    if (typeName.name) return typeName.name;
    if (typeName.right && typeName.right.name) return typeName.right.name;
    return void 0;
  }`
    );
    fs.writeFileSync(parserPath, content, 'utf8');
  }
}

// 2. Patch parseTopLevelType.js
const topLevelPath = path.resolve(__dirname, '../node_modules/@react-native/codegen/lib/parsers/typescript/parseTopLevelType.js');
if (fs.existsSync(topLevelPath)) {
  let content = fs.readFileSync(topLevelPath, 'utf8');
  if (!content.includes('function getTypeName(')) {
    content = content.replace(
      `'use strict';`,
      `'use strict';

function getTypeName(typeNameNode) {
  if (!typeNameNode) return undefined;
  if (typeNameNode.name) return typeNameNode.name;
  if (typeNameNode.right && typeNameNode.right.name) return typeNameNode.right.name;
  return undefined;
}`
    );
    content = content.replace(
      `const name = value.typeName.name;`,
      `const name = getTypeName(value.typeName);`
    );
    content = content.replace(
      `if (types[value.typeName.name]) {`,
      `const name = getTypeName(value.typeName); if (name && types[name]) {`
    );
    content = content.replace(
      `return getValueFromTypes(types[value.typeName.name], types);`,
      `return getValueFromTypes(types[name], types);`
    );
    content = content.replace(
      /case 'TSTypeReference':\s+if \(type\.typeName\.name === 'Readonly'\)/g,
      `case 'TSTypeReference': {
      const typeRefName = getTypeName(type.typeName);
      if (typeRefName === 'Readonly')`
    );
    content = content.replace(
      /} else if \(type\.typeName\.name === 'WithDefault'\)/g,
      `} else if (typeRefName === 'WithDefault')`
    );
    content = content.replace(
      /resolvedType\.typeName\.name === type\.typeName\.name/g,
      `getTypeName(resolvedType.typeName) === typeRefName`
    );
    fs.writeFileSync(topLevelPath, content, 'utf8');
  }
}

// 3. Patch componentsUtils.js
const utilsPath = path.resolve(__dirname, '../node_modules/@react-native/codegen/lib/parsers/typescript/components/componentsUtils.js');
if (fs.existsSync(utilsPath)) {
  let content = fs.readFileSync(utilsPath, 'utf8');
  if (!content.includes('(extractedTypeAnnotation.type === \'TSTypeReference\'')) {
    content = content.replace(
      /const type =\s+extractedTypeAnnotation\.elementType === 'TSTypeReference'[\s\S]*?extractedTypeAnnotation\.type;/g,
      `const type =
    (extractedTypeAnnotation.type === 'TSTypeReference'
      ? parser.getTypeAnnotationName(extractedTypeAnnotation)
      : undefined) ||
    (extractedTypeAnnotation.elementType
      ? (extractedTypeAnnotation.elementType.type === 'TSTypeReference'
          ? parser.getTypeAnnotationName(extractedTypeAnnotation.elementType)
          : extractedTypeAnnotation.elementType.type)
      : undefined) ||
    (extractedTypeAnnotation.typeName
      ? parser.getTypeAnnotationName(extractedTypeAnnotation)
      : undefined) ||
    extractedTypeAnnotation.type;`
    );
    content = content.replace(
      `parser.getProperties(property.typeName.name, types)`,
      `parser.getProperties(parser.getTypeAnnotationName(property), types)`
    );
    fs.writeFileSync(utilsPath, content, 'utf8');
  }
}
