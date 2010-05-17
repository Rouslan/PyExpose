<?xml version="1.0"?>
<xsl:stylesheet version="1.0"
        xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
        xmlns:xsd="http://www.w3.org/2001/XMLSchema"
        xmlns="http://www.w3.org/1999/xhtml"
        xmlns:html="http://www.w3.org/1999/xhtml"
        exclude-result-prefixes="xsd html">
    <xsl:output
        doctype-public="-//W3C//DTD XHTML 1.0 Strict//EN"
        doctype-system="http://www.w3.org/TR/xhtml1/xhtml1-strict.dtd"
        omit-xml-declaration="yes"/>

    <xsl:template match="/">
        <html>
            <head>
                <title>documentation</title>
                <style type="text/css">
                    body { font: small Verdana, sans-serif; }
                    dt { font-weight: bold; }
                    dd { margin-bottom: 1em; }
                </style>
            </head>
            <body>
                <h1>PyExpose Specification File Format</h1>
                <xsl:apply-templates select="xsd:annotation"/>
                <ul>
                    <xsl:for-each select="//xsd:element">
                        <li>
                            <a href="#element-{@name}">
                                <xsl:value-of select="@name"/>
                            </a>
                        </li>
                    </xsl:for-each>
                </ul>
                <xsl:apply-templates/>
            </body>
        </html>
    </xsl:template>

    <xsl:template name="datatype">
        <xsl:param name="type"/>
        <xsl:choose>
            <xsl:when test="$type='symbol'">"&lt;C++ symbol&gt;"</xsl:when>
            <xsl:when test="$type='arglist'">"&lt;Argument List&gt;"</xsl:when>
            <xsl:when test="$type='retsemantictype'">"&lt;Return Semantic&gt;"</xsl:when>
            <xsl:when test="$type='ident'">"&lt;Python Identifier&gt;"</xsl:when>
            <xsl:when test="$type='includelist'">"&lt;Include List&gt;"</xsl:when>
        </xsl:choose>
    </xsl:template>

    <xsl:template match="xsd:documentation/text()">
        <p><xsl:value-of select="."/></p>
    </xsl:template>

    <xsl:template match="xsd:documentation/node()">
        <xsl:element name="{name()}">
            <xsl:copy-of select="@*"/>
            <xsl:apply-templates/>
        </xsl:element>
    </xsl:template>

    <xsl:template match="xsd:element[@name]">
        <hr/>
        <h2>
            <a name="element-{@name}" id="element-{@name}">
                <xsl:value-of select="@name"/>
            </a>
        </h2>
        <p>
            <xsl:value-of select="xsd:annotation/xsd:documentation"/>
        </p>
        <xsl:if test="xsd:complexType/*/xsd:element">
            <p>
                Child elements:
                <xsl:for-each select="xsd:complexType/*/xsd:element">
                    <xsl:variable name="name">
                        <xsl:choose>
                            <xsl:when test="@ref">
                                <xsl:value-of select="@ref"/>
                            </xsl:when>
                            <xsl:otherwise>
                                <xsl:value-of select="@name"/>
                            </xsl:otherwise>
                        </xsl:choose>
                    </xsl:variable>
                    <xsl:if test="position() > 1">
                        <xsl:text>, </xsl:text>
                    </xsl:if>
                    <a href="#element-{$name}">
                        <xsl:value-of select="$name"/>
                    </a>
                </xsl:for-each>
            </p>
        </xsl:if>
        <xsl:if test="xsd:complexType/xsd:attribute">
            <h3>Attributes:</h3>
            <dl>
                <xsl:for-each select="xsd:complexType/xsd:attribute">
                    <dt>
                        <xsl:value-of select="@name"/>
                        <xsl:text> = </xsl:text>
                        <xsl:call-template name="datatype">
                            <xsl:with-param name="type" select="@type"/>
                        </xsl:call-template>
                    </dt>
                    <dd>
                        <xsl:value-of select="xsd:annotation/xsd:documentation"/>
                    </dd>
                </xsl:for-each>
            </dl>
        </xsl:if>
        <xsl:apply-templates select="xsd:complexType//xsd:element"/>
    </xsl:template>
</xsl:stylesheet>